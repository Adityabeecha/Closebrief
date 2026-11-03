# CloseBrief UI standard

The bar every screen has to clear. CloseBrief keeps its own look ("the auditor's
ledger": green ink on ledger paper, Newsreader for written commentary, IBM Plex
Mono for figures). This file is about the experience: what a finance lead sees
first, which words mean what, and what every screen must do.

Code lives in `ui/web/index.html` (tokens + CSS) and `ui/web/app.js`.

## 1. Principles

1. **The brief comes first.** CloseBrief's product is the written explanation of
   a number. On every metric surface the commentary sits above charts. Charts
   support the words, never the other way round.
2. **One accent, for action and "you are here".** `--accent` (ledger green) is
   for the primary button, the active nav item, the selected tab or period, and
   focus. Green-as-good uses `--green`, which happens to match in light mode;
   never rely on the accent to mean "good".
3. **Status always has a word.** Colour backs up a label; it is never the only
   signal. See section 3 for the exact words.
4. **Figures in mono, prose in serif, UI in sans.** `.nbi-num` for every figure
   so columns line up.
5. **Calm, like a report.** Thin borders, light shadows, no gradients behind
   data, motion of 120–200 ms that never carries meaning.
6. **Light and dark from tokens only.** No raw hex in new components; use the
   variables on `:root` / `:root[data-theme="dark"]`.

## 2. Formats (one of each, everywhere)

| Thing | Format | Helper |
|---|---|---|
| Money, compact | `$102K`, `$1.20M`, `−$370K` (real minus sign U+2212) | `fmt(v, unit)` |
| Percent change | `+4.1%`, `−0.2%` | `pct(v)` |
| Counts | `1,234` | `fmt(v, "count")` |
| Month | `Jun 2026` (short) / `June 2026` (long) | `fmtPeriod(p)` / `fmtPeriod(p, "long")` |
| Quarter / year | `Q2 2026` / `2026` | `fmtPeriod(p)` |
| Chart axis month | `Jun 26` | `fmtPeriod(p, "axis")` |
| Date | `01 Nov 2026` | `fmtDate(iso)` |

ISO strings (`2026-06`) stay in values, URLs and API calls; people never read
them.

## 3. Status words

A narrative has two separate checks. Show them as two separate things, with
these exact words.

| Check | States |
|---|---|
| Figures (guard) | **Figures verified** (green) · **Needs review** (amber, a figure couldn't be traced) · nothing shown when unchecked |
| Approval (review workflow) | **Not approved yet** (grey) · **Awaiting approval** (amber) · **Approved** (green) · **Changes requested** (red) |

Explanation confidence (high/medium/low) is about how well context explains the
move. It's written as "Explanation: low confidence", never next to a green
badge without that qualifier.

Metric movement: **Anomaly** (red) is the only attention flag on a card.

## 4. Layout

- Left menu grouped: **Close** (Dashboard, Digest, Review queue, Context
  library, Analytics), **Data** (Import), **Admin** (Cost, Notifications,
  Users). Counts beside items where there is a queue.
- Page head: title + one-line summary left, at most one primary action right
  (plus one secondary).
- Dashboard toolbar is one row: search and category chips left; KPI manager and
  period control right. Import lives in the menu and empty states.
- Phones (< 900 px): slide-in menu, 16 px gutters, full-width actions, no
  sideways page scroll; wide tables scroll inside their own box.

## 5. Every screen must

- **Have a URL.** Views are `#dashboard`, `#digest`…; a metric is
  `#metric=<name>&period=<YYYY-MM>`. Back returns to where you were, refresh
  keeps your place, and the tab title names the page
  (`Catering Revenue · Jun 2026 — Closebrief`).
- **Have three states:** loading (skeleton plus words saying what is happening,
  and a "still working" line after a few seconds), empty (says why and offers
  the next action), error (plain reason plus Retry).
- **Agree with itself.** After any action (generate, import, budget join,
  review), the counts in the head, the tiles, the banners and the cards are
  refreshed together.
- **Work by keyboard.** Anything clickable is reachable with Tab, opens with
  Enter/Space, and shows the focus ring (`:focus-visible`). Icon-only buttons
  have `aria-label`.
- **Fit fingers.** Tap targets at least 24 px, aim for 36 px on phones.

## 6. Charts

- Straight line segments between months (no smoothing: it invents values).
- No area fill unless the axis starts at zero.
- If the value axis does not start at zero, say so under the chart
  ("Axis starts at $50K").
- Title states the real span ("18-month trend"), not a fixed number.
- When a comparison is missing (no budget), say so in words instead of drawing
  half a chart.

## 7. Done means

Checked at 375 px and 1280 px, in light and dark, with the keyboard only for
the main flow, and `pytest tests/test_responsive_ui.py tests/test_ui_standard.py`
passing.
