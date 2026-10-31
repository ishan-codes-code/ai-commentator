# Obsidian AI — Design System

Product visual identity for the AI Commentator desktop application
(Electron renderer). This document states the design philosophy and rules.
Exact token values live in [DESIGN_TOKENS.md](DESIGN_TOKENS.md);
component usage rules in [COMPONENT_GUIDELINES.md](COMPONENT_GUIDELINES.md);
motion rules in [MOTION_GUIDELINES.md](MOTION_GUIDELINES.md).

## Identity

**Obsidian AI**: a dark, cinematic, desktop-grade experience for an
AI-powered esports commentator. The UI should feel like professional
broadcast software: quiet surfaces, precise borders, one confident accent,
and data presented without decoration.

## Principles

1. **Restraint first.** Dark, premium, technical. Every decorative element
   must justify its pixels. When in doubt, remove.
2. **Hierarchy through spacing and type**, not through color noise.
   Generous whitespace, clear heading/body/label ranks, consistent 4px-base
   rhythm.
3. **Glass with purpose.** Glassmorphism marks layered, floating, or
   elevated surfaces (panels, dialogs, popovers) — never flat page
   background. One ambient glow per view at most, kept subtle.
4. **One accent, one signal.** Electric Violet carries primary actions and
   active states. Ice Blue carries secondary/technical signals (timers,
   live indicators, links). Status colors speak only for status.
5. **Desktop software, not a marketing site.** No hero sections, no
   feature grids, no oversized rounded cards, no emoji iconography.

## Color direction

- Canvas: near-black `#09090B`; surfaces step up in small increments
  (`#111114` → `#18181F` → `#22222B`).
- Text: near-white foreground; two muted ranks for secondary and subtle
  content. Muted text is for metadata, never for primary actions.
- Borders are hairlines (`rgba(255,255,255,0.09)`); glass borders are
  slightly brighter (`rgba(255,255,255,0.12)`).
- Primary Electric Violet `#8B5CF6`, secondary Ice Blue `#38BDF8`.
  Status: green `#22C55E`, amber `#F59E0B`, red `#EF4444`.
- All component styling references **semantic tokens**
  (`bg-background`, `text-muted-foreground`, `border-border`, …),
  never raw palette values. See [DESIGN_TOKENS.md](DESIGN_TOKENS.md).

## Typography

- **Inter** for UI text (headings, body, labels, controls).
- **Geist Mono** for timers, technical values, and statistics.
- Semibold headings, regular body. One size scale with clear ranks;
  body copy stays readable at desktop window sizes.
- Fonts ship with the app via `@fontsource` packages (bundled, works
  offline) — no runtime webfont fetching.

## Glassmorphism rules

- Glass = `rgba(24,24,31,0.78)` fill + ~16px backdrop blur + glass border.
- Use glass only on genuinely layered surfaces: app panel, dialogs,
  popovers, floating status elements.
- Flat content surfaces use solid `surface`/`elevated` fills, not glass.
- Never animate large blurred surfaces continuously (see
  [MOTION_GUIDELINES.md](MOTION_GUIDELINES.md)); blur is expensive on
  the GPU.
- Text on glass must pass contrast: foreground or muted-foreground only,
  never subtle-foreground on glass.

## Elevation and depth

1. Canvas (background) — flat.
2. Surface — cards and sections.
3. Glass panel — the main app panel, floating above canvas with a soft,
   static ambient glow.
4. Elevated — hover/raised controls, dialog bodies.
5. Overlay — popovers, tooltips, toasts (highest, with scrim where modal).

Shadows are soft and dark, never colored. Ambient glow is violet-tinted,
low-opacity, static, and limited to one surface per view.

## Visual anti-patterns (do not ship these)

- Excessive gradients, neon-everywhere, glowing text.
- Identical glow on every card; glass on every surface.
- `bg-clip-text` gradient headlines; purple-to-blue hero gradients.
- Oversized rounding on everything (one radius per surface rank —
  see tokens).
- Bento grids, 3-card feature grids, hero + eyebrow + two-CTA layouts.
- Emojis as icons (use Lucide), `space-x-*` stacks (use `gap`),
  `hover:scale-105` card lifts.
- Perpetual animation of blurred/glowing layers; motion that ignores
  `prefers-reduced-motion`.

## Scope

This milestone establishes the foundation (tokens, primitives, app shell,
empty states). It is not a complete application: no video-selection flow,
no dashboard data, no backend services. Explicit exclusions and the staged
plan live in [UI_ROADMAP.md](UI_ROADMAP.md).
