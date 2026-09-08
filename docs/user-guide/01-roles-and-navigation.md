# 1. Roles, sign-in, and navigation

[User guide](../user-guide.md) · Previous: [Guide index](../user-guide.md) · Next: [Projects and data](02-projects-and-data.md)

## Roles

Every role lands on the same **Home**. The role decides what is *on* it, not
where it lives, so a link to a task or a project means the same thing to
everyone who can open it.

| Role | Home tabs | Typical responsibilities |
| --- | --- | --- |
| Requester | **My projects** | Create projects, register server-readable data, describe requested work, monitor progress, and inspect results |
| Manager | **Awaiting review** · **Projects to approve** · **Assigned to me** · **Open cases** · **Shares** | Approve projects, manage access and teams, assign tasks, review submissions, manage shares, and reopen or reset work |
| Annotator | **Assigned to me** · **Needs revision** · **Done** · **Feedback** · **Cases in my projects** | Edit assigned volumes, save drafts, record hard cases, respond to feedback, and submit snapshots |

Each tab is a saved query over the same list. The number on a tab is how many
rows it holds — that count is the only "needs attention" signal there is.

Pages and API results are permission-aware. Not seeing a button usually means
the current role or project relationship does not permit the action; it is not
necessarily a loading failure.

## Sign-in and development accounts

Choose the portal role on the sign-in page, enter credentials, and press **Sign
in**. When development accounts are enabled, selecting an account only fills
the form and selects the appropriate portal tab. It never authenticates or
navigates automatically.

The red **Clear all existing files** action is a development-only destructive
reset. It is not an account choice and should not be used to fix an individual
task. It clears application data while preserving configured development
identities. Production deployments normally hide this control.

## Global navigation

The navigation bar holds four entries, and every one of them is a *place*:

- **Home** — what is waiting on you, across every project.
- **Projects** — every project you can see. Server-scoped: managers see all,
  requesters their own, annotators the ones they work on.
- **People** — role-scoped collaborators, project teams, and eligibility.
- The username opens **Profile**; **Log out** ends the session.

`Register Data` is not in the bar, because it is an action rather than a place.
It sits on the pages that own it: Home, the Projects list, and a project's
**Data** tab.

Hard cases are not in the bar either. A case belongs to a project, so it lives
on that project's **Cases** tab, and Home surfaces the ones that concern you.

Every detail page carries a breadcrumb (`Cortex study / Tasks / cortex_01 #42`)
whose every segment is a link. **Back** still returns through application
history when possible, then falls back to the logical parent. The product name
is display-only.

List filters and the selected tab live in the URL, so a narrowed list is a link
you can send. Sharing or bookmarking such an authenticated URL does not grant
access.

## Lists look the same everywhere

Tasks, hard cases, and projects are rendered by one list. A row reads:

```
● cortex_01 z1–256                                          #42
  Submitted 2 days ago by alice · awaiting review          alice
  [needs split]
```

- a coloured **state dot** (with a text label for screen readers),
- the **title**, then the **`#id`** that addresses it,
- one line saying **what last happened and who did it**,
- category chips, and the person responsible.

Above the rows, `N Open / M Closed` doubles as a filter, alongside dropdowns
that only ever offer values actually present in the list.

## A task page is a conversation with a sidebar

Opening a row from any list lands on that unit of work. `/tasks/42` is the same
page for a manager and an annotator — a link you paste means one thing to
everyone who can open it.

```
Cortex study  /  Tasks  /  cortex_01 z1–256  #42        ← breadcrumb, every segment a link

cortex_01 z1–256   #42   [Submitted]                    [View] [Annotate]
assigned to alice · volume cortex_01 · 3 submissions

  Conversation 6     Labels     History 3
```

- **Conversation** (the default) is the history, oldest first: assigned,
  submitted round 1, the reviewer's decision and comment, submitted round 2.
  **What you do next is at the end of it**, where you finished reading — the
  review form for a manager, Annotate and the offline-upload link for the
  assignee. Whoever cannot act sees the state rather than a disabled button.
- **Labels** is the annotation canvas. **View** is read-only; **Annotate**
  paints. These two also sit in the header, because they are the primary verbs
  of this application and nothing on the page outranks them.
- **History** is the earlier submission rounds and their decisions.

The right-hand sidebar holds assignee, priority, difficulty, deadline, task
type, frame range, dataset, label type, measured time, instructions, and links
to the hard cases raised on this volume. It is metadata, so it sits beside the
narrative rather than interrupting it.

A hard case page has the same skeleton — title, `#id`, state, the discussion
with its reply box at the end, metadata in a sidebar — with the canvas above,
because for a hard case the picture is the subject.

**Unmeasured time reads `-`, never `0m`.** A volume whose annotation began
before time tracking exists cannot report a real total, and crediting somebody
with zero effort they were never measured on would be worse than saying so.

## Three permissions that are easy to confuse

1. **Project access** lets a person see a project and collaborate on its
   permitted content.
2. **Working-team eligibility** makes an annotator available for assignment on
   that project.
3. **Task assignment** names the one annotator who owns the current
   whole-volume editing task.

Granting access does not assign work. Adding a person to a team does not by
itself expose every project. Managers should verify all three states when an
annotator cannot find a task.

## Common first checks

- If redirected after opening a page, confirm the account role and project
  membership.
- If **Annotate** is absent, confirm the task is open and assigned to the current
  annotator. Managers may inspect more broadly, but inspection does not accrue
  the annotator's time.
- If the page seems stale after a role or access change, return to **Home** and
  reopen the project.
- A list that looks empty may simply be filtered: the dropdowns and the
  Open / Closed strip persist in the address bar, so a link somebody sent you
  arrives with their filters applied. Clear them to see everything.

[User guide](../user-guide.md) · Next: [Projects and data](02-projects-and-data.md)
