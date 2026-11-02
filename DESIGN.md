---
name: Closebrief
description: Month-end variance commentary set as an auditor's lead schedule, every figure ticked to its source.
colors:
  ledger-green: "#1e6e50"
  ledger-green-deep: "#124632"
  pad: "#e7efe2"
  pad-deep: "#dde8d6"
  sheet: "#f5f9f2"
  graphite: "#1d2621"
  graphite-2: "#34423a"
  graphite-3: "#4d5e54"
  red-pencil: "#b33a27"
  amber-pencil: "#8a5a00"
  blue-pencil: "#2b5aa8"
  on-green: "#ffffff"
  night-pad: "#0f1713"
  night-sheet: "#16211b"
  night-green: "#5cbf8f"
typography:
  display:
    fontFamily: "Schibsted Grotesk, system-ui, sans-serif"
    fontSize: "clamp(42px, 4.5vw, 66px)"
    fontWeight: 850
    lineHeight: 0.96
    letterSpacing: "-0.04em"
  headline:
    fontFamily: "Schibsted Grotesk, system-ui, sans-serif"
    fontSize: "clamp(34px, 3.6vw, 52px)"
    fontWeight: 850
    lineHeight: 1
    letterSpacing: "-0.035em"
  title:
    fontFamily: "Schibsted Grotesk, system-ui, sans-serif"
    fontSize: "27px"
    fontWeight: 800
    lineHeight: 1.15
    letterSpacing: "-0.02em"
  body:
    fontFamily: "Schibsted Grotesk, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Red Hat Mono, ui-monospace, monospace"
    fontSize: "12px"
    fontWeight: 600
    letterSpacing: "0.12em"
  figure:
    fontFamily: "Red Hat Mono, ui-monospace, monospace"
    fontSize: "18px"
    fontWeight: 500
    fontFeature: "tnum"
rounded:
  pencil: "3px"
  sheet: "4px"
  mark: "6px"
spacing:
  xs: "8px"
  sm: "16px"
  md: "28px"
  lg: "56px"
  xl: "84px"
components:
  button-primary:
    backgroundColor: "{colors.ledger-green}"
    textColor: "{colors.on-green}"
    rounded: "{rounded.sheet}"
    padding: "17px 26px"
  button-primary-hover:
    backgroundColor: "{colors.ledger-green-deep}"
    textColor: "{colors.on-green}"
  button-quiet:
    backgroundColor: "{colors.sheet}"
    textColor: "{colors.graphite}"
    rounded: "{rounded.sheet}"
    padding: "17px 26px"
  card:
    backgroundColor: "{colors.sheet}"
    textColor: "{colors.graphite}"
    rounded: "{rounded.sheet}"
    padding: "18px"
  chip-delta:
    typography: "{typography.label}"
    rounded: "{rounded.pencil}"
    padding: "3px 8px"
  input:
    backgroundColor: "{colors.pad}"
    textColor: "{colors.graphite}"
    rounded: "{rounded.sheet}"
    padding: "12px 13px"
---

# Design System: Closebrief

## Overview

**Creative North Star: "Working Papers"**

Closebrief is an auditor's working papers for the month-end close. The page is a green columnar analysis pad; KPIs sit on it as a lead schedule; each figure carries a pencil tick that says how it was checked, and a legend explains every mark. Commentary is the reviewer's note clipped beside the schedule, and every piece of work closes on a Prepared / Reviewed / Approved sign-off. The system exists to make one promise visible: the AI writes the words, it never computes the numbers.

The world is dense where finance people expect density (schedules, figures, references) and quiet everywhere else. Colour is pencil, not paint: ledger green rules and ticks, graphite text, and red pencil reserved for the exceptions that matter. Nothing is decorative; every rule, reference code and tick is doing a job a controller would recognise. The look was chosen over a generic SaaS template, a playful style, a dark developer-tool look and a plain corporate one; it should never drift toward any of those.

Light is the daytime desk; dark is the same pad under a desk lamp ("night shift"), built from the same tokens.

**Key Characteristics:**
- A columnar pad ground (green column rules every 120px over faint 32px ruling).
- Loose sheets with square-ish corners resting on the pad, never rounded app cards.
- Every number in mono tabular figures; every number with a reference or a tick.
- Drawn pencil tick marks with a legend, never glyph icons.
- Draft prose dashed, approved prose solid.

## Colors

A restrained pencil palette on a tinted green pad: one brand green does the structural work, graphite carries text, three pencils carry state.

### Primary
- **Ledger Green** (#1e6e50): rules under headers, ticks, the primary action, active navigation, links. The brand colour and a PRODUCT.md commitment.
- **Deep Ledger Green** (#124632): hover and pressed state of the primary action.

### Secondary
- **Amber Pencil** (#8a5a00): a deviation worth watching before it becomes an exception; warnings and conflicts.
- **Blue Pencil** (#2b5aa8): reserved for "new since you last looked". Defined, not yet used in the build.

### Tertiary
- **Red Pencil** (#b33a27): exceptions past the materiality threshold, failed checks, destructive actions. Nothing else.

### Neutral
- **Columnar Pad** (#e7efe2): page ground, with column rules (`rgba(30,110,80,.26)`) and faint ruling (`rgba(30,110,80,.11)`).
- **Pad Header Wash** (#dde8d6): table headers and sheet header bands; sunken areas.
- **Loose Sheet** (#f5f9f2): cards, notes, schedules and panels laid on the pad.
- **Graphite Ink** (#1d2621): primary text and headings.
- **Soft Graphite** (#34423a): body copy and commentary.
- **Pencil Grey** (#4d5e54): labels, meta, empty sign-off slots. 5.1:1 on the pad.
- **Night shift**: Night Pad (#0f1713), Night Sheet (#16211b), Lamplit Green (#5cbf8f) with near-black text on green actions; every pencil has a lighter night value in `ui/web/theme.css`.

### Named Rules
**The Red Pencil Rule.** Red marks an exception past the materiality threshold, a failed figure check, or a destructive action. It is never decoration, never a category colour, never emphasis.

**The One Signal Rule.** Blue pencil means "new since you last looked" and nothing else. If it appears for any other reason, it's wrong.

**The 4.5 Rule.** Every text colour clears 4.5:1 on pad, pad-deep and sheet in both themes; the tokens were chosen against that, so use the tokens.

## Typography

**Display Font:** Schibsted Grotesk (with system-ui)
**Body Font:** Schibsted Grotesk (with system-ui)
**Label/Mono Font:** Red Hat Mono (with ui-monospace)

**Character:** A heavy newspaper-grade grotesque for statements, set tight and confident; a plain mono for everything that is a figure or a reference, so columns line up and numbers read as data.

### Hierarchy
- **Display** (850, clamp(42px, 4.5vw, 66px), 0.96): the home page's lead headline, two lines, balanced.
- **Headline** (850, clamp(34px, 3.6vw, 52px), 1): section heads on the home page; app page titles use the same voice at clamp(26px, 2.5vw, 36px).
- **Title** (800, 27px, 1.15): sheet headings: the reviewer's note, panel titles, closing sheet.
- **Body** (400, 17px, 1.5; app 14–15px, 1.6 for commentary): explanatory copy and commentary, 46–56ch measure.
- **Label** (Red Hat Mono 600, 12px, 0.12em, uppercase): W/P references, column headers, sheet header bands, sign-off captions. 11px is the floor.
- **Figure** (Red Hat Mono 500, tabular): every number on every surface.

### Named Rules
**The Mono Means Data Rule.** Mono is for figures, references and codes. Never set prose, buttons or headings in mono.

**The No-Kicker Rule.** Labels live in header bands and column heads, never as a small line floating above a heading.

## Layout

Content sits in a 1344px container (48px gutters, 16px on phones) on the full-bleed pad. The home page is a sequence of schedules separated by 2px ledger-green rules, each section 84px tall in padding (56px on phones), with a two-column intro (heading left, one paragraph right). The first viewport splits 1.62 : 1 between the lead schedule and the reviewer's note, and must hold the tick legend at 1440×900. The app keeps its sidebar plus content layout (max 1200px content), on the same pad with faint ruling.

Breakpoints: 1100px (lead grid stacks), 900px (sidebar becomes a drawer), 720px (phone: tables become stacked rows with labels, header action shortens to "Demo").

## Elevation & Depth

Depth is paper on paper: sheets rest on the pad with a short contact line and a soft, low shadow; nothing floats far.

### Shadow Vocabulary
- **Sheet lift** (`box-shadow: 0 1px 0 rgba(29,38,33,.06), 0 8px 22px -14px rgba(29,38,33,.35)`): every resting sheet: cards, notes, schedules, panels.
- **Sheet lift, hover** (`box-shadow: 0 1px 0 rgba(29,38,33,.06), 0 16px 30px -18px rgba(29,38,33,.45)`): a clickable sheet under the pointer, with a 2px rise.

### Named Rules
**The Paper Rule.** Only sheets cast shadows. Buttons, chips and controls are flat; no hard offset shadows, no glow.

## Shapes

Square paper with a whisper of softness: 4px on sheets, buttons and inputs, 3px on pencil chips (deltas, seals, anomaly tags), 6px only on the logo mark. Structure comes from rules, not containers: a 2px ledger-green rule under every header row and between home sections, 1px column-rule lines between rows and columns.

## Components

### Buttons
- **Shape:** squared (4px).
- **Primary:** Ledger Green fill, white text, 800 weight; 17px 26px on the home page, 9px 15px in the app.
- **Hover / Focus:** fill deepens to Deep Ledger Green; 1px press on active; 2px Ledger Green focus ring, 3px offset.
- **Quiet:** transparent with a 2px graphite border; fills graphite on hover.
- **App default:** a sheet-coloured button with a pencil-grey border that darkens on hover.

### Chips
- **Style:** mono 11.5px, 3px corners, tinted wash in the state's pencil (green good, red bad, pad-deep flat).
- **State:** the "Figures verified" seal carries the pencil tick; "Needs review" uses amber.

### Cards / Containers
- **Corner Style:** 4px.
- **Background:** Loose Sheet.
- **Shadow Strategy:** Sheet lift (see Elevation).
- **Border:** 1px column rule; Ledger Green on hover.
- **Internal Padding:** 18px; KPI sheets open with a header band (pad-deep wash, 1px rule) holding the name, sparkline and review box.

### Inputs / Fields
- **Style:** pad-coloured fill, 1px pencil-grey border, 4px corners; mono uppercase label above.
- **Focus:** 2px Ledger Green outline.
- **Error:** red pencil border plus a message under the field.

### Navigation
- **Style:** the sidebar is the binder spine: Pad Header Wash with a 2px Ledger Green right rule, mono section labels (Close, Data, Admin), 14px items. Active item is a sheet-coloured tab in Ledger Green. Phones get a slide-in drawer.

### Lead Schedule (signature)
A table of KPIs: mono tracked headers in Ledger Green over a 2px rule, figures right-aligned in mono, a final tick column with a drawn pencil tick per row. Exceptions set the whole row in red pencil with a cross. Footnote numbers link rows to the reviewer's note; hovering or focusing a footnoted figure washes its row and redraws its tick.

### Sign-off Strip (signature)
Prepared / Reviewed / Approved in three ruled cells under a 2px Ledger Green rule, mono captions, empty slots in pencil grey. Closes every note and the home page.

### Review Box
On each KPI sheet header: an empty 22px box with a pencil-grey border until the commentary is approved, then a green pencil tick on a green wash.

## Do's and Don'ts

### Do:
- **Do** give every figure a mono tabular face and, where it is checked, a pencil tick from the legend.
- **Do** underline draft commentary dashed (1px, 5px offset) and approved commentary solid green.
- **Do** keep exceptions in red pencil and nothing else red.
- **Do** put labels in header bands and column heads, with a 2px ledger-green rule under them.
- **Do** draw icons as SVG strokes in the pencil style (2.4–2.6 stroke, round caps).
- **Do** label sample data as sample data wherever a visitor could mistake it for real.

### Don't:
- **Don't** use rounded app cards, coloured left-edge stripes or gradient backgrounds on sheets.
- **Don't** put a small eyebrow label above a heading.
- **Don't** use hard offset shadows or glow on buttons.
- **Don't** use glyph characters (✓ ✕ × ▾ ‹ ›) as icons.
- **Don't** set prose or buttons in mono.
- **Don't** drift toward the rejected looks: generic SaaS template, playful, dark developer tool, plain corporate.
