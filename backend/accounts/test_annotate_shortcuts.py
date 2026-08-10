"""Per-user annotate tool shortcuts: validation, persistence, and who may set them."""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import UserProfile
from accounts.shortcuts import (
    ANNOTATE_SHORTCUT_TOOL_IDS,
    DEFAULT_ANNOTATE_SHORTCUTS,
    effective_annotate_shortcuts,
    normalize_annotate_shortcuts,
)
from core.choices import UserRole


class NormalizeAnnotateShortcutsTests(TestCase):
    def test_defaults_cover_every_tool(self):
        self.assertEqual(
            set(DEFAULT_ANNOTATE_SHORTCUTS), set(ANNOTATE_SHORTCUT_TOOL_IDS)
        )

    def test_verify_owns_f_and_flood_fill_moves_to_l(self):
        self.assertEqual(DEFAULT_ANNOTATE_SHORTCUTS["verify"], "f")
        self.assertEqual(DEFAULT_ANNOTATE_SHORTCUTS["flood_fill"], "l")
        self.assertEqual(DEFAULT_ANNOTATE_SHORTCUTS["solo"], "s")

    def test_a_partial_map_is_completed_from_the_defaults(self):
        result = normalize_annotate_shortcuts({"brush": "n"})
        self.assertEqual(result["brush"], "n")
        self.assertEqual(result["select"], DEFAULT_ANNOTATE_SHORTCUTS["select"])
        self.assertEqual(set(result), set(ANNOTATE_SHORTCUT_TOOL_IDS))

    def test_letters_are_lower_cased_and_trimmed(self):
        self.assertEqual(normalize_annotate_shortcuts({"brush": " N "})["brush"], "n")

    def test_empty_means_no_shortcut_rather_than_invalid(self):
        self.assertEqual(normalize_annotate_shortcuts({"brush": ""})["brush"], "")

    def test_two_tools_may_not_share_a_letter(self):
        with self.assertRaisesMessage(ValueError, "already used by"):
            normalize_annotate_shortcuts({"brush": "q", "eraser": "q"})

    def test_a_conflict_names_both_tools_so_it_can_be_fixed(self):
        with self.assertRaisesMessage(ValueError, "Brush"):
            normalize_annotate_shortcuts({"brush": "q", "merge": "q"})

    def test_multi_character_and_non_letter_bindings_are_refused(self):
        for bad in ("ab", "1", "+", "ß"):
            with self.subTest(bad=bad):
                with self.assertRaisesMessage(ValueError, "single letter"):
                    normalize_annotate_shortcuts({"brush": bad})

    def test_an_unknown_tool_is_refused_rather_than_stored(self):
        with self.assertRaisesMessage(ValueError, "Not an annotate tool"):
            normalize_annotate_shortcuts({"save": "s"})

    def test_effective_falls_back_to_defaults_for_junk(self):
        # A profile holding nonsense must still leave the editor usable.
        self.assertEqual(effective_annotate_shortcuts("not a map"), DEFAULT_ANNOTATE_SHORTCUTS)
        self.assertEqual(
            effective_annotate_shortcuts({"brush": 7})["brush"],
            DEFAULT_ANNOTATE_SHORTCUTS["brush"],
        )

    def test_legacy_explicit_flood_f_is_preserved_without_a_verify_conflict(self):
        legacy = {tool: value for tool, value in DEFAULT_ANNOTATE_SHORTCUTS.items()
                  if tool not in {"verify", "solo"}}
        legacy["flood_fill"] = "f"
        result = effective_annotate_shortcuts(legacy)
        self.assertEqual(result["flood_fill"], "f")
        self.assertEqual(result["verify"], "")


class AnnotateShortcutProfileApiTests(TestCase):
    @staticmethod
    def _person(username, role, *, superuser=False):
        # A post_save signal (accounts.signals.ensure_user_profile) already made
        # a default profile, so set the role on that row — and refresh, or the
        # user object keeps the signal-created profile cached and `get_role`
        # answers with the default role instead of this one.
        maker = User.objects.create_superuser if superuser else User.objects.create_user
        user = maker(username, password="x")
        UserProfile.objects.update_or_create(user=user, defaults={"role": role})
        user.refresh_from_db()
        return user

    def setUp(self):
        self.annotator = self._person("shortcut-annotator", UserRole.ANNOTATOR)
        self.manager = self._person("shortcut-manager", UserRole.MANAGER, superuser=True)
        self.requester = self._person("shortcut-requester", UserRole.REQUESTER)

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_defaults_are_served_before_anything_is_customised(self):
        response = self._client(self.annotator).get(reverse("api-me"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["annotate_shortcuts"], DEFAULT_ANNOTATE_SHORTCUTS)
        self.assertTrue(response.data["can_customize_shortcuts"])

    def test_an_annotator_can_save_and_reread_a_binding(self):
        client = self._client(self.annotator)
        response = client.patch(
            reverse("api-people-me"),
            {"annotate_shortcuts": {"brush": "n"}},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["annotate_shortcuts"]["brush"], "n")
        # Persisted on the account, not in the browser.
        self.assertEqual(
            client.get(reverse("api-me")).data["annotate_shortcuts"]["brush"], "n"
        )

    def test_a_manager_may_customise_too(self):
        response = self._client(self.manager).patch(
            reverse("api-people-me"),
            {"annotate_shortcuts": {"merge": "n"}},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["annotate_shortcuts"]["merge"], "n")

    def test_a_requester_has_no_tools_to_bind(self):
        response = self._client(self.requester).patch(
            reverse("api-people-me"),
            {"annotate_shortcuts": {"brush": "n"}},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            self._client(self.requester).get(reverse("api-me")).data["can_customize_shortcuts"]
        )

    def test_a_conflicting_binding_is_rejected_with_a_usable_message(self):
        response = self._client(self.annotator).patch(
            reverse("api-people-me"),
            {"annotate_shortcuts": {"brush": "q", "eraser": "q"}},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("already used by", str(response.data))

    def test_saving_shortcuts_leaves_the_rest_of_the_profile_alone(self):
        client = self._client(self.annotator)
        client.patch(reverse("api-people-me"), {"display_name": "Ann"}, format="json")
        client.patch(
            reverse("api-people-me"),
            {"annotate_shortcuts": {"brush": "n"}},
            format="json",
        )
        self.assertEqual(client.get(reverse("api-me")).data["display_name"], "Ann")

    def test_two_accounts_keep_distinct_maps_across_refreshes(self):
        other = self._person("shortcut-other", UserRole.ANNOTATOR)
        first_client = self._client(self.annotator)
        second_client = self._client(other)
        first_client.patch(
            reverse("api-people-me"),
            {"annotate_shortcuts": {"brush": "n"}},
            format="json",
        )
        second_client.patch(
            reverse("api-people-me"),
            {"annotate_shortcuts": {"brush": "q"}},
            format="json",
        )
        self.assertEqual(
            first_client.get(reverse("api-me")).data["annotate_shortcuts"]["brush"],
            "n",
        )
        self.assertEqual(
            second_client.get(reverse("api-me")).data["annotate_shortcuts"]["brush"],
            "q",
        )
