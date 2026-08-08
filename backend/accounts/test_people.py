"""People — the role-scoped collaboration surface (acceptance row E of
``progress/history/05-submit-people-hardcases.md``).

The thing worth pinning down here is that every panel is a projection of one
relation — shared projects — rather than three independently-maintained
rosters: an annotator's "managers" are the managers of projects they hold
tasks on, their "peers" are the other annotators on those same projects, and
the manager's customer list is the requesters who registered projects.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import AnnotatorProfile, UserProfile
from accounts.services import people_overview, person_detail
from annotation.models import AnnotationTask
from core.choices import TaskStatus, TaskType, UserRole
from core.dev_data import STANDARD_ACCOUNTS, seed_standard_data
from projects.services import create_project
from volumes.models import Volume

User = get_user_model()


class PeopleOverviewTests(TestCase):
    def setUp(self):
        self.manager = self._user("p_mgr", UserRole.MANAGER)
        self.alice = self._user("p_alice", UserRole.ANNOTATOR, annotator=True)
        self.bob = self._user("p_bob", UserRole.ANNOTATOR, annotator=True)
        self.zoe = self._user("p_zoe", UserRole.ANNOTATOR, annotator=True)
        self.customer = self._user("p_cust", UserRole.REQUESTER)

        # One shared project (alice + bob), one unrelated project (zoe).
        self.shared = create_project(
            title="Shared", created_by=self.customer, reviewed=True
        )
        self.shared.reviewed_by = self.manager
        self.shared.save(update_fields=["reviewed_by"])
        self.other = create_project(title="Other", created_by=self.manager)

        self._task(self.shared, self.alice)
        self._task(self.shared, self.bob)
        self._task(self.other, self.zoe)

    def _user(self, name, role, annotator=False):
        user = User.objects.create_user(name, password="x")
        UserProfile.objects.filter(user=user).update(role=role)
        if annotator:
            AnnotatorProfile.objects.create(user=user, is_active_annotator=True)
        return User.objects.get(pk=user.pk)

    def _task(self, project, annotator, status=TaskStatus.ASSIGNED):
        volume = Volume.objects.create(
            project=project, name=f"v{annotator.username}", image_path="x.tif",
            shape_z=2, shape_y=2, shape_x=2,
        )
        return AnnotationTask.objects.create(
            project=project, volume=volume, assigned_to=annotator,
            z_start=0, z_end=2, y_end=2, x_end=2,
            task_type=TaskType.MANUAL_ANNOTATION, status=status,
        )

    def _client(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    # --- annotator ----------------------------------------------------------

    def test_annotator_sees_project_managers_and_peers_only(self):
        data = people_overview(self.alice)
        self.assertEqual(data["role"], UserRole.ANNOTATOR)
        self.assertEqual([m["username"] for m in data["managers"]], ["p_mgr"])
        self.assertEqual([p["username"] for p in data["peers"]], ["p_bob"])
        # zoe works on a different project — not a peer.
        self.assertNotIn("p_zoe", [p["username"] for p in data["peers"]])

    def test_annotator_peer_entry_names_the_shared_project(self):
        peer = people_overview(self.alice)["peers"][0]
        self.assertEqual([p["title"] for p in peer["projects"]], ["Shared"])
        self.assertEqual(peer["stats"]["shared_projects"], 1)

    def test_annotator_sees_their_own_counts(self):
        self._task(self.shared, self.alice, status=TaskStatus.APPROVED)
        stats = people_overview(self.alice)["me"]["stats"]
        self.assertEqual(stats["assigned"], 2)
        self.assertEqual(stats["approved"], 1)
        self.assertEqual(stats["active"], 1)

    # --- manager ------------------------------------------------------------

    def test_manager_sees_annotators_with_workload(self):
        data = people_overview(self.manager)
        names = [a["username"] for a in data["annotators"]]
        self.assertEqual(names, ["p_alice", "p_bob", "p_zoe"])
        alice = next(a for a in data["annotators"] if a["username"] == "p_alice")
        self.assertEqual(alice["stats"]["assigned"], 1)
        self.assertEqual([p["title"] for p in alice["projects"]], ["Shared"])

    def test_manager_roster_includes_annotators_with_no_work_yet(self):
        idle = self._user("p_idle", UserRole.ANNOTATOR, annotator=True)
        names = [a["username"] for a in people_overview(self.manager)["annotators"]]
        self.assertIn(idle.username, names)

    def test_manager_sees_requesters_and_the_projects_they_registered(self):
        data = people_overview(self.manager)
        self.assertEqual([r["username"] for r in data["requesters"]], ["p_cust"])
        customer = data["requesters"][0]
        self.assertEqual([p["title"] for p in customer["projects"]], ["Shared"])
        self.assertEqual(customer["stats"]["projects"], 1)

    def test_manager_sees_the_last_decision_per_annotator(self):
        from annotation.services import (
            reject_submission,
            submit_annotation,
        )
        from django.core.files.uploadedfile import SimpleUploadedFile

        task = AnnotationTask.objects.filter(assigned_to=self.alice).first()
        submission = submit_annotation(
            task=task,
            annotator=self.alice,
            label_file=SimpleUploadedFile("a.tif", b"II*\x00"),
        )
        reject_submission(submission, reviewer=self.manager)

        alice = next(
            a
            for a in people_overview(self.manager)["annotators"]
            if a["username"] == "p_alice"
        )
        self.assertEqual(alice["stats"]["last_decision"], "rejected")
        self.assertEqual(alice["stats"]["reviews_rejected"], 1)
        self.assertEqual(alice["stats"]["submissions"], 1)

    # --- requester ----------------------------------------------------------

    def test_requester_sees_their_projects_and_who_runs_them(self):
        data = people_overview(self.customer)
        self.assertEqual([p["title"] for p in data["projects"]], ["Shared"])
        self.assertEqual(data["projects"][0]["managers"], ["p_mgr"])
        self.assertEqual([m["username"] for m in data["managers"]], ["p_mgr"])
        self.assertEqual(
            sorted(p["username"] for p in data["peers"]), ["p_alice", "p_bob"]
        )

    # --- endpoints ----------------------------------------------------------

    def test_overview_endpoint_is_role_scoped(self):
        body = self._client(self.alice).get("/api/people/overview/").json()
        self.assertEqual(body["role"], UserRole.ANNOTATOR)
        self.assertEqual(body["annotators"], [], "annotators panel is manager-only")
        self.assertEqual([m["username"] for m in body["managers"]], ["p_mgr"])

    def test_overview_requires_authentication(self):
        self.assertIn(
            APIClient().get("/api/people/overview/").status_code, (401, 403)
        )

    def test_profile_patch_updates_the_editable_fields_only(self):
        resp = self._client(self.alice).patch(
            "/api/people/me/",
            {
                "display_name": "Alice N.",
                "contact_note": "Async, EU hours",
                "role": UserRole.MANAGER,  # must be ignored
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["display_name"], "Alice N.")
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.profile.role, UserRole.ANNOTATOR)
        self.assertEqual(self.alice.profile.contact_note, "Async, EU hours")

    def test_person_detail_endpoint(self):
        body = self._client(self.bob).get("/api/people/p_alice/").json()
        self.assertEqual(body["username"], "p_alice")
        self.assertEqual([p["title"] for p in body["projects"]], ["Shared"])
        self.assertEqual(
            self._client(self.bob).get("/api/people/nobody/").status_code, 404
        )
        self.assertIsNone(person_detail(self.bob, "nobody"))


class SeededRequesterTests(TestCase):
    """The demo roster must include two requesters, or the manager's People
    view has no customers to show on a fresh database."""

    def test_seed_creates_two_requesters(self):
        result = seed_standard_data(log=lambda *a, **k: None)
        self.assertEqual(result["requesters"], ["requester1", "requester2"])
        for name in result["requesters"]:
            self.assertEqual(User.objects.get(username=name).profile.role,
                             UserRole.REQUESTER)

    def test_seeded_accounts_get_a_display_name(self):
        seed_standard_data(log=lambda *a, **k: None)
        self.assertEqual(
            User.objects.get(username="requester1").profile.display_name,
            "Dr. Rivera",
        )

    def test_standard_accounts_still_lists_the_manager_and_annotators(self):
        self.assertEqual(STANDARD_ACCOUNTS["manager"], UserRole.MANAGER)
        self.assertEqual(
            [n for n, r in STANDARD_ACCOUNTS.items() if r == UserRole.ANNOTATOR],
            ["alice", "bob", "carol", "dave"],
        )
