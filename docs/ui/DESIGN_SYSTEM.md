# Obsidian Studio — Design System

Product visual identity for the AI Commentator desktop application
(Electron renderer). This document states the design philosophy and rules.
Exact token values live in [DESIGN_TOKENS.md](DESIGN_TOKENS.md);
component usage rules in [COMPONENT_GUIDELINES.md](COMPONENT_GUIDELINES.md);
motion rules in [MOTION_GUIDELINES.md](MOTION_GUIDELINES.md).

## Identity

**Obsidian Studio**: a premium, cinematic, professional AI broadcasting
workspace. The UI should feel like broadcast control software: quiet
near-black surfaces, precise hairlines, confident typography, and data
presented without decoration. Restraint is the brand — every decorative
element must justify its pixels. This is not merely a darker theme; the
system is built on typographic hierarchy, consistent spacing, deliberate
proportions, and flat layered surfaces.

## Principles

1. **Solid before glass.** Layout surfaces are solid charcoal with
   hairline separation. Glassmorphism is reserved for true overlays
   (dialogs, popovers) — never for sidebars, panels, or cards.
2. **Hierarchy through type and space.** One type scale, tight tracking
   on headings, 13px UI body, 12px metadata, mono for technical values.
   Generous but consistent spacing on a 4px base.
3. **Monochrome by default.** White and charcoal establish action and
   selection hierarchy. Color is reserved for semantic status feedback.
4. **Flat depth.** Separation comes from surface steps and 1px borders,
   not shadows, glows, or stacked floating panels. One ambient treatment
   at most, kept static and subtle.
5. **Desktop software, not a website.** 12–15px type range, compact
   controls, honest empty states, no heroes, no feature grids, no emoji.

## Color direction

- Canvas: near-black `#09090A`; surfaces step up deliberately
  (`#101011` → `#171718` → `#202021`).
- Borders are solid `#2A2A2B` hairlines — no translucent glow borders
  on layout surfaces.
- Text: `#F1F1F1` primary; `#A1A1A4` secondary body; `#77777B`
  metadata.
- Primary actions use a light neutral surface with dark text; active
  navigation uses a restrained charcoal surface.
  Status: green `#22C55E`, amber `#F59E0B`, red `#EF4444`.
- All component styling references **semantic tokens**
  (`bg-background`, `text-muted-foreground`, `border-border`, …),
  never raw palette values. See [DESIGN_TOKENS.md](DESIGN_TOKENS.md).

## Typography

- **Inter** for UI text; **Geist Mono** for timers, technical values,
  paths, and statistics. Both bundled via `@fontsource` (offline-safe).
- Scale: 15px semibold section titles (`-0.01em` tracking), 13px UI
  body/labels, 12px metadata, 11px micro-labels (sidebar footer only).
- Semibold headings, regular body. Tight leading on headings
  (`leading-tight`), relaxed `1.5` body line-height for readability.
- Never set marketing-hero sizes (20px+) for in-app headings; never use
  uppercase tracked kickers as section decoration.

## Surface and border rules

- Sidebar, header bar, cards: solid fills, 1px `border-border`.
- The header is a bar, not a panel: flat surface, bottom hairline only.
- Elevation is conveyed by surface step + border, with at most a faint
  1px shadow (`shadow-panel`); `shadow-overlay` is for modal layers.
- `backdrop-blur-glass` exists for future dialogs/popovers — do not
  apply blur to layout.
- Text on elevated surfaces stays at secondary rank or above.

## Visual anti-patterns (do not ship these)

- Glassmorphism on sidebars, panels, or cards; glow on every surface.
- Stacked floating panels — one contained surface per view, flat inside.
- Gradient headlines, `bg-clip-text`, purple-to-blue hero gradients.
- Oversized rounding (see radius tokens); identical radius everywhere.
- Bento grids, 3-card feature grids, hero + eyebrow + two-CTA layouts.
- Emojis as icons (Lucide only), `space-x-*` stacks (use `gap`),
  `hover:scale-105` card lifts.
- Perpetual animation, animated gradients/blur, motion that ignores
  `prefers-reduced-motion`.

## Scope

This milestone covers the design system and application shell. It adds no
product features: no project management (the dashboard reserves a marked
planned area), no artifact browsing, no TTS, no live capture. Explicit
staging lives in [UI_ROADMAP.md](UI_ROADMAP.md).
