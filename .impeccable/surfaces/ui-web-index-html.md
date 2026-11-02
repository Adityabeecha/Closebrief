---
version: 1
slug: "ui-web-index-html"
primary_target: "ui/web/index.html"
related_targets: ["ui/web/home.html"]
---

## Scope

The whole app at `/app` (Operate): every screen moves into one structure, the Month Workspace. Inherits the Review Instrument world (DESIGN.md: Precision light / Night dark, Schibsted Grotesk + Red Hat Mono, Closing Ring). The home page (`ui/web/home.html`) follows: its preview shows the month workspace and its flow section mirrors the close checklist.

User decision (2026-11-02): after two rounds of four whole-product structures (Runway, Board, Inbox, One Instrument; Bridge, Month Workspace, The Brief, Prepare/Read) the user delegated the choice; Month Workspace was chosen on fit to the close, executive reading, phone, reuse of the three-pane dashboard and build cost. It absorbs: Runway's stage checklist and progress (Overview tab, sidebar progress bars), The Brief's document reading (Digest and Board pack tabs), Prepare/Read's role landing (executives and viewers land on Digest in reading view), Inbox's clickable fix items (Overview checklist rows open the exact fix).

## Direction contract

THESIS: Each month's close is one workspace. The sidebar lists closes (June 2026 in progress, May shared) with a progress bar each, then three cross-month places (Analyze, Context library, Data); settings live under the avatar. Inside a month, tabs: Overview, KPIs, Context, Digest, Board pack, Data. It refuses the nine-link sidebar of unrelated pages and the grid of equal cards.

STRUCTURE: Overview = what's left before the month can be shared (import, review movers, add reasons, approve drafts, share), each row a link to its fix, plus the month at a glance and an activity feed. KPIs = the existing three-pane review instrument (queue, document, inspector), the old Review Queue folded in. Context = notes for this month in list | note | KPIs it feeds. Digest = the executive digest as a document with checked figures and traces. Board pack = in-app preview plus download (PDF, PowerPoint). Data = import and mapping, unmapped columns as fix items. Analyze = cross-month analytics. Settings = costs, notifications, users, workspace.

URLS: `#month=YYYY-MM&tab=overview|kpis|context|digest|boardpack|data`, `&metric=<name>` opens a KPI in the KPIs tab; `#analyze`, `#library`, `#data`, `#settings=costs|notifications|users`. Every old hash (`#dashboard`, `#digest`, `#context`, `#analytics`, `#review`, `#import`, `#costs`, `#notifications`, `#admin`, `#metric=…&period=…`) redirects to its new home.

FIRST VIEWPORT: sidebar (logo, Closes list with June selected and its progress bar, Across months group, avatar); month header (title "June 2026 close", status pill, search Ctrl K, primary action that changes with the close stage); tabs; Overview content: checklist card left, at-a-glance and activity right.

SIGNATURE: the close's progress is visible everywhere it matters: the month's bar in the sidebar, the checklist on Overview, and the primary action in the month header naming the next step ("Add 3 reasons", "Approve 5 drafts", "Send the digest").

FINISH: detector clean on every tab in both themes and at 375px; three states (loading, empty, error) per tab; DESIGN.md updated with the workspace components; before/after comparison against 7602f47.
