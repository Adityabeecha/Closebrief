---
name: Closebrief
description: A review instrument for the month-end close, in two lightings; every figure traced to its source.
colors:
  accent: "#047857"
  accent-hover: "#065f46"
  on-accent: "#ffffff"
  bg: "#fbfcfd"
  surface: "#ffffff"
  surface-2: "#f8fafc"
  surface-3: "#f1f4f8"
  line: "#e6e9ef"
  line-strong: "#cfd5df"
  text: "#0b1220"
  text-2: "#344054"
  text-3: "#596579"
  verified: "#067647"
  verified-wash: "#e8f8ef"
  review: "#a1450a"
  review-wash: "#fff6e5"
  exception: "#c8281c"
  exception-wash: "#fdeeed"
  select: "#2563eb"
  select-wash: "#e8f0fe"
  night-bg: "#141311"
  night-surface: "#1c1a17"
  night-surface-2: "#191815"
  night-surface-3: "#26231f"
  night-line: "#2e2b27"
  night-line-strong: "#44403c"
  night-text: "#f3efe8"
  night-text-2: "#d6d3d1"
  night-text-3: "#a8a29e"
  night-accent: "#34d399"
  night-accent-hover: "#6ee7b7"
  night-on-accent: "#06120c"
  night-review: "#fbbf24"
  night-exception: "#fb7185"
  night-select: "#8ab4ff"
typography:
  display:
    fontFamily: "Schibsted Grotesk, system-ui, sans-serif"
    fontSize: "clamp(44px, 6vw, 80px)"
    fontWeight: 750
    lineHeight: 1
    letterSpacing: "-0.045em"
  headline:
    fontFamily: "Schibsted Grotesk, system-ui, sans-serif"
    fontSize: "clamp(32px, 3.6vw, 48px)"
    fontWeight: 750
    lineHeight: 1.04
    letterSpacing: "-0.035em"
  title:
    fontFamily: "Schibsted Grotesk, system-ui, sans-serif"
    fontSize: "clamp(24px, 2.2vw, 32px)"
    fontWeight: 750
    lineHeight: 1.08
    letterSpacing: "-0.03em"
  body:
    fontFamily: "Schibsted Grotesk, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "Schibsted Grotesk, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 700
    letterSpacing: "0.06em"
  figure:
    fontFamily: "Red Hat Mono, ui-monospace, monospace"
    fontSize: "14px"
    fontWeight: 600
    fontFeature: "tnum"
rounded:
  sm: "8px"
  md: "12px"
  lg: "16px"
  pill: "999px"
spacing:
  xs: "8px"
  sm: "16px"
  md: "24px"
  lg: "40px"
  xl: "104px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
    rounded: "{rounded.sm}"
    padding: "15px 24px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
    textColor: "{colors.on-accent}"
  button-ghost:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.sm}"
    padding: "15px 24px"
  pane:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.lg}"
    padding: "20px"
  queue-row:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text}"
    rounded: "{rounded.sm}"
    padding: "10px"
  queue-row-selected:
    backgroundColor: "{colors.select-wash}"
    textColor: "{colors.text}"
  status-pill:
    typography: "{typography.label}"
    rounded: "{rounded.pill}"
    padding: "3px 10px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.sm}"
    padding: "12px 13px"
---

# Design System: Closebrief

## Overview

**Creative North Star: "The Review Instrument"**

Closebrief is an instrument for reviewing a month's close, not a dashboard of tiles. Every screen is organised around one loop: a queue of KPIs ordered by what needs a decision, the selected KPI's commentary read as a document, and an inspector beside it that traces every figure in that document to its source. The home page shows the same instrument working, so a visitor sees the product before they read about it.

It ships in two lightings of one instrument. **Precision** (light) is cool white, ink and slate with an electric-blue selection; **Night close** (dark) is warm charcoal with an emerald action and amber and rose states. The theme follows the visitor's system. Colour carries state and nothing else: emerald is the action and "verified", amber is "needs review", rose is "exception", blue is "selected". Every state colour travels with a word.

It must not drift toward paper or ledger textures (the previous direction), a single flat green, a grid of equal KPI cards, neon-on-black developer tooling, or bland grey corporate.

**Key Characteristics:**
- Queue | document | inspector, the same three parts on every KPI surface.
- Hairline structure (1px lines), soft shadows on panes only, 8 to 16px radii, pill status chips.
- Schibsted Grotesk set tight for words; Red Hat Mono for every figure.
- Checked figures inside prose are highlighted and link to their trace.
- Light and dark from the same semantic tokens in `ui/web/theme.css`.

## Colors

Restrained: calm neutrals, one action colour, and four state colours that always carry a word.

### Primary
- **Ledger Emerald** (#047857 light / #34d399 night): the primary action, the brand ring of the logo, active navigation icons. The brand green commitment from PRODUCT.md.
- **Deep Emerald** (#065f46 / #6ee7b7 night): hover state of the primary action.

### Secondary
- **Selection Blue** (#2563eb / #8ab4ff night): the selected queue row, focus rings, inline links. Means "you are here", never a state.

### Tertiary
- **Review Amber** (#a1450a / #fbbf24 night): "needs review", "draft ready", deviations to look at.
- **Exception Rose** (#c8281c / #fb7185 night): anomalies, failed figure checks, destructive actions.
- **Verified** (#067647 / #34d399 night): figures and commentary that passed the check, approved items.

### Neutral
- **Cool White** (#fbfcfd) / **Warm Charcoal** (#141311): page ground.
- **Pane** (#ffffff / #1c1a17), **Pane Inset** (#f8fafc / #191815), **Pane Sunken** (#f1f4f8 / #26231f): the three surface depths.
- **Ink** (#0b1220 / #f3efe8), **Slate** (#344054 / #d6d3d1), **Muted** (#596579 / #a8a29e): text tiers; Muted still clears 4.5:1 on every surface.
- **Hairline** (#e6e9ef / #2e2b27) and **Strong hairline** (#cfd5df / #44403c): structure.

### Named Rules
**The State Has A Word Rule.** No colour stands alone: every dot, pill or tint is paired with its label ("Exception", "Needs review", "Approved", "4 of 4 figures verified").

**The One Action Rule.** Emerald fills only the primary action of a view. Secondary actions are ghost buttons on a hairline.

**The 4.5 Rule.** Every text and state token clears 4.5:1 on every surface in both themes, including state text on its own wash.

## Typography

**Display Font:** Schibsted Grotesk (with system-ui)
**Body Font:** Schibsted Grotesk (with system-ui)
**Label/Mono Font:** Red Hat Mono (with ui-monospace)

**Character:** A firm grotesque set tight for headlines and calm at reading sizes, beside a plain mono that makes every number line up and read as data.

### Hierarchy
- **Display** (750, clamp(44px, 6vw, 80px), 1): the home page headline only.
- **Headline** (750, clamp(32px, 3.6vw, 48px), 1.04): home page section heads.
- **Title** (750, clamp(24px, 2.2vw, 32px), 1.08): app page and KPI titles.
- **Body** (400, 17px, 1.55; commentary 18px, 1.75): copy and the commentary document; 36 to 40em measure.
- **Label** (700, 12px, 0.06em, uppercase): queue group headings, pane headings, table headers.
- **Figure** (Red Hat Mono 600, tabular): every number, including highlighted figures inside prose.

### Named Rules
**The Mono Means Data Rule.** Mono is for figures and codes only; never for prose, buttons or headings.

**The No-Kicker Rule.** Labels head panes and groups; they never float as a small line above a headline.

## Layout

The app is a sidebar (global navigation) beside the workspace. The workspace header (title, period, share, generate) spans the top; below it a two-column split: a 340px sticky queue column and the document column, which itself splits into the commentary (fluid) and a 320px inspector. Below 1360px the inspector stacks under the commentary; below 900px the workspace shows one pane at a time: the queue at `#dashboard`, the open KPI at `#metric=…` with Back, and the dashboard header hides while a KPI is open. On wide screens the first KPI in review order opens automatically.

The home page is a centred hero over a 1180px live preview of the workspace, then sections at 104px intervals (72px on phones) in a 1240px container with 40px gutters (16px on phones).

## Elevation & Depth

Depth comes from three surface tints and hairlines; shadows appear only on panes and the home preview.

### Shadow Vocabulary
- **Pane** (`box-shadow: 0 1px 2px rgba(16,24,40,.05), 0 12px 32px -18px rgba(16,24,40,.28)`; night: `0 1px 0 rgba(0,0,0,.35), 0 18px 44px -24px rgba(0,0,0,.85)`): panes, panels, the sign-in sheet.
- **Showcase** (`box-shadow: 0 2px 4px rgba(16,24,40,.04), 0 40px 80px -30px rgba(16,24,40,.35)`): the home page's workspace preview only.

### Named Rules
**The Flat Controls Rule.** Buttons, chips, rows and inputs are flat. No offset shadows, no glow, no coloured edge stripes; selection is a tinted fill with a 1px ring.

## Shapes

Softly rounded and consistent: 8px on buttons, inputs and queue rows; 12px on banners; 16px on panes and the preview; full pills for every status and filter chip. Status dots are 8px circles.

## Components

### Buttons
- **Shape:** gently rounded (8px).
- **Primary:** emerald fill, white text (night: dark text on light emerald), 700 weight; 15px 24px on the home page, 9px 15px in the app.
- **Hover / Focus:** fill deepens; 1px press; 2px selection-blue focus ring.
- **Ghost:** pane fill with a strong hairline; sunken fill on hover.

### Chips
- **Status pills:** label weight, full pill, state colour on its wash ("Exception", "4 of 4 figures verified", "Draft").
- **Queue counts:** pane-coloured pills with a state dot ("0 exceptions", "7 to review", "0 approved").
- **Filters:** full pills; the selected one fills with ink.

### Cards / Containers
- **Corner Style:** 16px.
- **Background:** Pane.
- **Shadow Strategy:** Pane shadow (see Elevation).
- **Border:** 1px hairline.
- **Internal Padding:** 20px; pane headings are uppercase labels.

### Inputs / Fields
- **Style:** pane fill, strong hairline, 8px corners, label above in 13px slate.
- **Focus:** selection-blue border with a 3px blue wash.
- **Error:** exception-rose border and a message under the field.

### Navigation
- **Sidebar:** inset fill, 14px items, uppercase group labels (Close, Data, Admin); the active item is a pane-coloured tab with an emerald icon. Phones get a slide-in drawer.

### KPI Queue (signature)
Grouped in review order (Exceptions, Needs review, Approved, No data this period) with uppercase group labels and counts. Each row: an 8px state dot, the KPI name, the variance that matters in mono (vs plan, else period-on-period), and a one-line status in words ("$102K · MoM · Draft ready"). The selected row takes the blue wash and ring.

### Document + Inspector (signature)
The commentary reads as a document at 18px with checked figures highlighted; beside it the inspector lists Figures (actual, budget, vs plan, vs prior period, year on year), the trend and budget-vs-actual charts, and the trace for each figure. Hovering or focusing a highlighted figure lights its trace.

### Logo
The **Closing Ring**: an emerald ring closing around an ink check (night: light emerald and warm white), beside the wordmark "close" (800) + "brief" (500). App icon and favicon: the ring and check on an ink square. Files in `ui/web/brand/`.

## Do's and Don'ts

### Do:
- **Do** order every KPI list by what needs a decision: exceptions first.
- **Do** pair every state colour with its word.
- **Do** set every figure in Red Hat Mono, and highlight checked figures inside prose.
- **Do** build both themes from the semantic tokens in `ui/web/theme.css`; never hard-code a hex in a component.
- **Do** show selection as the blue wash and 1px ring.
- **Do** label sample data wherever a visitor could take it for real.

### Don't:
- **Don't** lay KPIs out as a grid of equal cards.
- **Don't** use coloured left or right edge stripes, offset shadows or glows on controls.
- **Don't** put a small label above a headline.
- **Don't** use text glyphs as icons; draw them as SVG.
- **Don't** return to paper, ruled-pad or ledger textures, a single flat green, neon-on-black, or grey corporate.
