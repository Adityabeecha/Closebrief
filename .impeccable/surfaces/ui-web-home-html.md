---
version: 1
slug: "ui-web-home-html"
primary_target: "ui/web/home.html"
related_targets: ["ui/web/index.html"]
---

## Scope

Public home page at `/` (Persuade) and the app at `/app` (Operate), one world. Supersedes the Working Papers direction (still the committed base on `redesign/closebrief-world`). Visitors: finance operators at month-end, executives reading commentary, hiring reviewers. Home primary action: open the live demo; secondary: sign in. App task: work the month's KPIs from exceptions down, read each draft, check its figures, approve. Proof: the real eval results, the demo datasets, the product's own UI. No invented customers, testimonials, pricing or certifications; sample figures labelled.

User decisions: layout = three panes (queue, document, inspector); home = centred hero with a wide app preview; theme follows the system, light = Precision palette, dark = Night palette. User dislikes: paper/old-fashioned, flat single green, unchanged card-grid app, not premium.

## Direction contract

THESIS: Closebrief is a review instrument, not a dashboard: the month's KPIs queue up by what needs a decision, each one's commentary reads as a document, and an inspector beside it traces every figure to its source. It refuses the grid of equal KPI cards and the marketing split-hero with a framed screenshot.

OWN-WORLD: Two lightings of one instrument. Light: cool white ground (#fbfcfd), white panes, ink #0b1220, slate greys, hairline #eaecf0, selection electric blue (#2563eb), verification emerald (#059669). Dark: warm charcoal ground (#141311), panes #1c1a17, hairlines #2e2b27, ink #f3efe8, emerald (#34d399) primary action and verified figures. Both: amber means needs review, rose means exception, emerald-washed highlights mark checked figures inside prose. Schibsted Grotesk set tight for headings and UI, Red Hat Mono tabular for every figure; 10 to 16px radii on panes, pill status chips, hairline structure, soft radial light only on the home page.

STORY: A visitor reads "Close the books. The brief is already written.", sees the real three-pane workspace with an exception selected and its figures traced, and opens the demo close. In the app the analyst works down the queue (Exceptions, Needs review, Approved), reads the draft, confirms each figure's trace in the inspector, approves, and the KPI moves to Approved.

FIRST VIEWPORT: Top bar (wordmark left; Product, Verification, Sign in; emerald "Open the demo" right). Centred pill stating the core promise, a two-line centred headline (~76px), one line of subcopy, primary "Open the demo close" plus a quiet "How verification works". Below, a 1180px live HTML preview of the workspace rises from the fold: queue left with Bad debt selected, document centre with highlighted figures, inspector right with figure traces.

FORM: user-pinned fusion of round-2 directions A (Precision Light) and B (Night Close), chosen after rendered home and app sketches; a pinned direction beats the roll. Original roll for this project: seed:75f914db1434e18a. Signature interaction: hovering or focusing a highlighted figure in the document lights its trace row in the inspector, and the reverse.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
