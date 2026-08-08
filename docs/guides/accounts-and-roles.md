# Accounts and roles

The sign-in page opens the portal associated with the account's role.

| Role | Main responsibilities |
| --- | --- |
| Requester | Create projects, register data, inspect project progress |
| Manager | Review projects, manage access/teams, assign tasks, review submissions, manage shares |
| Annotator | Edit assigned tasks, save drafts, submit for review |

All authenticated roles can use **People** and can browse **Hard Cases** for
projects they can access. A manager can also open annotation work directly.

Use **Create one** on the login page to register as an annotator or requester.
Manager accounts are created and governed by the deployment operator.

## Your profile

Selecting your name in the top-right of the navbar opens **Profile**. Every role
can edit their display name and contact note there.

Annotators and managers also get **Annotate shortcuts**: the letter used with
`Cmd` (macOS) or `Ctrl` (Windows/Linux) to switch each of the 13 annotate tools.
The bindings are saved on the account, so they follow the user between browsers
and machines. Requesters do not annotate and see a note instead of the editor.
See [Keyboard shortcuts and tips](keyboard-and-tips.md#your-own-tool-shortcuts).

## Development accounts

The **Development accounts** section is present only when mock-development
login is enabled by the server. It lists the configured disposable accounts.
Selecting one fills the username, password, and portal tab; it does not sign in
or navigate. Press **Sign in** explicitly.

The destructive development reset is also hidden unless both required server
flags are enabled. It is for disposable environments only. Production reset
keeps its separate authentication, backup, maintenance, confirmation, and
deployment-identity checks.

## Access versus assignment

The project's **Access** list combines explicit project membership, working-team
membership, and task assignment. Explicit membership grants participation
without workload; the working team grants participation and assignment
eligibility; a task assignment identifies the single annotator responsible for
a volume. The Access table labels which route brought each person into the
project and shows working-team members even when they have no tasks yet.
