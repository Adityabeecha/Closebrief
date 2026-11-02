---
version: 1
slug: "ui-web-home-html"
primary_target: "ui/web/home.html"
related_targets: ["ui/web/index.html"]
---

## Scope

Public home page at `/` (Persuade). The app moves to `/app` and inherits the same world as an Operate surface (dashboard = lead schedule, metric detail = supporting schedule, review queue = sign-off). Visitors: finance operators, executives reading commentary, and hiring reviewers. Primary action: open the live read-only demo. Secondary: sign in. Proof available: the real eval results (Recall@5 / Faithfulness / Groundedness 100% on a 15-case golden set), the demo datasets, and the product's own UI. No customers, testimonials, pricing or certifications may be invented; demo figures are labelled demo data.

## Direction contract

THESIS: The home page is a working lead schedule, not a hero over feature cards: CloseBrief proves its promise the way auditors do, by ticking every figure to its source and leaving a legend that says what each mark means. It refuses the split hero with a product screenshot and the row of three feature cards.

OWN-WORLD: Pale green columnar analysis pad (#e7efe2 ground, green column rules, faint horizontal ruling) with ledger green #1e6e50 ink for rules, headers and the primary action; graphite #1d2621 for text. Hand-drawn pencil tick marks (SVG) with a numbered legend. Red pencil #c2412d reserved for exceptions past the materiality threshold. W/P reference codes in tracked mono caps. A Prepared / Reviewed / Approved sign-off strip. Heavy grotesque display, mono tabular figures. Draft prose underlined dashed, approved prose solid.

STORY: A visitor sees a real-looking month-end lead schedule, notices every figure is ticked and one exception is red, reads the reviewer's note whose figures footnote back to the rows, reads the legend "The AI writes the words. It never computes the numbers.", then opens the demo close. Below: how a close moves through import, compute, draft, check, review, share; the evidence (eval results, guard rules); and a closing sign-off strip with the same action.

FIRST VIEWPORT: Top rule bar: wordmark with W/P ref left; "How every figure is checked" and "Sign in" right; the primary action "Open the demo close" as a solid green tab. Left two-thirds: tracked kicker "Lead schedule · June 2026 close · demo data", display headline "Variance commentary, ticked and tied." (~76px), then a five-row KPI schedule (KPI, actual, budget, variance, tick) in mono figures. Right third: the reviewer's note card with superscript tick references and the sign-off strip. Bottom rule: the tick-mark legend carrying the core promise.

FORM: Working Papers (auditor's working papers and lead schedules), position 4 of 7 on the first grounded list, assigned by the roll and chosen by the user after seeing rendered first viewports. Raises kept: unreviewed variance stays marked until ticked (gate board); draft dashed, approved solid (pattern envelope); red pencil only past the threshold (VU meter); every figure wears its reference label (cutaway). Signature interaction: hovering or focusing a footnoted figure in the reviewer's note pencils in its tick and highlights its schedule row, the guard's trace made visible. seed:75f914db1434e18a

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
