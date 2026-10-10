# Account roles and active workspaces

This reference describes the current implementation. User instructions are in
[roles and navigation](../user-guide/01-roles-and-navigation.md); changes must
preserve [product invariants](../product-invariants.md).

## Identity, capability and scope

| Concept | Stored/resolved by | Behavior |
| --- | --- | --- |
| Base identity | `accounts.models.UserProfile.role`; superuser override in `base_role` | Annotator, manager or requester; assistant grants do not replace this value |
| Assistant capability | `UserProfile.is_assistant_manager`, default false | An active annotator may also select Manager; no staff/superuser grant |
| Available workspaces | `accounts.roles.available_roles` | Dual account: Annotator and Manager; other identities retain their base workspace |
| Active workspace | Validated token-request `X-Mito-Role`, then request-local `_active_role` | Existing `get_role`/permission helpers and scoped querysets use it; missing header uses base role |
| Primary manager | `is_primary_manager`: base manager identity or superuser | May grant/revoke assistant access; no singular lab-owner designation |
| Durable manager membership | `has_manager_role` | Used for project-manager identity even while an assistant uses Annotator mode |
| Team manager | `TeamMembership.role` | Separate team standing; does not grant application-wide assistant capability |

Manager mode has global application-manager scope, not a per-project delegation
model. Annotator mode keeps ordinary project/assignment scope and read-only
Measurements access; Manager mode permits the existing management actions and
explicit measurement runs. Grant/revoke is reserved for primary identities even
when an assistant is using Manager mode. Existing superuser-only reset gates
remain superuser-only.

## API and errors

| Endpoint | Input/output | Authorization and state |
| --- | --- | --- |
| `GET /api/auth/me/` | Existing user fields plus `available_roles`, `is_assistant_manager`, `can_manage_assistant_managers`; `role` is effective workspace | Authenticated; no account mutation |
| `POST /api/auth/role/` | `{ "role": "manager" }` or another available workspace; returns current-user shape | Authenticated; validates capability, returns 403 for an unavailable role; does not persist a shared token preference |
| `PATCH /api/people/<id>/assistant-manager/` | Boolean `{ "enabled": true }` or false; returns id/grant value | Primary managers only; active base annotator target required; invalid boolean 400, ineligible target 404, unauthorized actor 403 |
| `GET /api/people/overview/` | Role-scoped lists including `assistant_managers`; dual users remain in `annotators` | Uses current request role; public recipients have no roster access |
| `PATCH /api/people/me/` | Existing editable display/contact/shortcut fields | Does not update base role or assistant capability |

Normal token authentication requirements still apply. An invalid token is 401.
An unavailable `X-Mito-Role` is denied with 403 before endpoint execution.
Grant/revoke locks the target profile inside a transaction. A changed value adds
an `account.assistant_manager_changed` AuditEvent with actor, target and boolean
metadata. Repeating the same value returns success without another event.
Assignments, labels, submissions, memberships and passwords are not rewritten.

## Browser state and revocation

`frontend/src/api/client.ts` stores the mode in sessionStorage, alongside an
independent existing localStorage auth token. Shared decoded/raw-fetch clients
send `X-Mito-Role`; authenticated chunk authorization requests do too. Separate
tabs select independently. Logout clears that tab's mode, and ordinary login
starts at the account's base role.

The username dropdown exposes Profile and workspace choices only when applicable.
Switching validates via the API, stores the approved mode, then reloads to Home.
The confirmation reminds users to save first; it neither saves nor submits, and
pending UI input can be lost. A rejected switch retains the existing mode.
Full reload prevents role-scoped lists/forms from surviving a switch.

Revocation denies subsequent token-authenticated manager requests. Already issued
responses or downloaded data cannot be recalled, and it does not cancel jobs
already created. On reload/refresh, `fetchMe` clears a stale mode after 403 and
retries the base role without discarding the token. Existing open UI may display
stale controls until refreshed; the backend still rejects unauthorized actions.
After a new grant, an already signed-in user reloads to discover the new choices.

Session authentication and Django administration use the durable base identity;
the token-workspace header does not create an admin/staff account. Public share
routes keep their separate token scope and read-only guarantees.

## UI and contribution boundaries

People renders assistants in both rosters. The Assistant managers group is
foldable even at one person and has no Time report. Ordinary annotators' People/
person pages omit Time; managers keep the Annotators report. Each card has one
role summary; account-access controls are last, separated from Time by spacing
and a divider. Profile displays both identities in either workspace.

For changes:

- Authorize current requests through effective-role predicates and existing
  project/volume scope. Keep durable identity checks separate from workspace
  permission checks; do not accidentally remove assistants from assignment lists.
- Restrict delegation with `is_primary_manager`, not just `is_manager`.
- Reuse authenticated clients for mode headers and reload scoped state on switch.
  UI booleans/catalog visibility cannot replace server checks.
- Preserve explicit saves, default-false grants, audit history and production
  no-demo/reset gates. Migration `accounts/0014` is additive and auto-promotes nobody.

Implementation: [roles](../../backend/accounts/roles.py),
[authentication](../../backend/accounts/authentication.py),
[API](../../backend/accounts/api.py), [people services](../../backend/accounts/services.py),
[current-user serializer](../../backend/accounts/serializers.py),
[client](../../frontend/src/api/client.ts), [auth API](../../frontend/src/api/auth.ts),
[Navbar](../../frontend/src/components/Navbar.tsx) and
[People](../../frontend/src/pages/PeoplePage.tsx).

Evidence: [backend authorization tests](../../backend/accounts/test_assistant_managers.py),
[client recovery tests](../../frontend/src/api/workspace.test.ts),
[People tests](../../frontend/src/pages/PeoplePage.test.tsx),
[profile tests](../../frontend/src/pages/ProfilePage.test.tsx) and
[browser workflows](../../frontend/e2e/scientific-workbench.spec.ts).
