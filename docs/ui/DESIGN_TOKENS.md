# Obsidian AI — Design Tokens

Single source of truth for token **values and mappings**. Design intent
lives in [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md); usage rules in
[COMPONENT_GUIDELINES.md](COMPONENT_GUIDELINES.md).

Implementation: CSS custom properties + Tailwind CSS v4 `@theme` in
`electron/src/index.css`. Components reference semantic utilities only
(`bg-background`, `text-foreground`, `border-border`, …).

## Raw palette

| Token name      | Value                    | Role                              |
| --------------- | ------------------------ | --------------------------------- |
| `obsidian-950`  | `#09090B`                | Application canvas                |
| `obsidian-900`  | `#111114`                | Base surface                      |
| `obsidian-850`  | `#18181F`                | Glass surface (solid equivalent)  |
| `obsidian-800`  | `#22222B`                | Elevated surface                  |
| `violet`        | `#8B5CF6`                | Primary accent (Electric Violet)  |
| `ice`           | `#38BDF8`                | Secondary accent (Ice Blue)       |
| `success`       | `#22C55E`                | Success status                    |
| `warning`       | `#F59E0B`                | Warning status                    |
| `danger`        | `#EF4444`                | Danger status                     |
| `ink`           | `#FAFAFA`                | Primary text                      |
| `ink-muted`     | `#A1A1AA`                | Secondary text                    |
| `ink-subtle`    | `#71717A`                | Tertiary text / metadata          |
| `line`          | `rgba(255,255,255,0.09)` | Hairline border                   |
| `line-glass`    | `rgba(255,255,255,0.12)` | Border on glass surfaces          |
| `glass-fill`    | `rgba(24,24,31,0.78)`    | Glass panel fill (blur ≈ 16px)    |

## Semantic mapping

| Semantic token        | Tailwind utility          | Value source          |
| --------------------- | ------------------------- | --------------------- |
| `background`          | `bg-background`           | `obsidian-950`        |
| `foreground`          | `text-foreground`         | `ink`                 |
| `card` / `card-fore.` | `bg-card` / `text-card…`  | `obsidian-900` / ink  |
| `popover`             | `bg-popover`              | `obsidian-850`        |
| `primary`             | `bg-primary`              | `violet`              |
| `primary-foreground`  | `text-primary-foreground` | `ink`                 |
| `secondary`           | `bg-secondary`            | `ice` (dimmed 15%)    |
| `muted`               | `bg-muted` / `text-muted…`| `obsidian-800` / ink-muted |
| `subtle`              | `text-subtle`             | `ink-subtle` (metadata only) |
| `accent`              | `bg-accent`               | `obsidian-800`        |
| `border` / `input`    | `border-border`           | `line`                |
| `ring`                | `focus-visible:ring-ring`  | `violet`              |
| `success`             | `text-success` / `bg…`    | `success`             |
| `warning`             | `text-warning`            | `warning`             |
| `destructive`         | `bg-destructive`          | `danger`              |
| `glass`               | `bg-glass`                | `glass-fill`          |
| `elevated`            | `bg-elevated`             | `obsidian-800`        |
| `surface`             | `bg-surface`              | `obsidian-900`        |

Status colors are used only for status. Muted text never carries primary
actions. Focus rings always use `ring` (violet).

## Spacing

Base unit **4px** (Tailwind default scale). Named applications:

| Use                 | Token / class | Value |
| ------------------- | ------------- | ----- |
| Main panel padding  | `p-5`         | 20px  |
| Sidebar width       | `w-60`        | 240px |
| Card padding (std)  | `p-4`         | 16px  |
| Control gaps        | `gap-2`/`gap-3` | 8/12px |

Use the scale (`gap-*`, `p-*`), never ad-hoc pixel values.

## Radius

| Surface          | Token / class    | Value |
| ---------------- | ---------------- | ----- |
| Base (`--radius`)| `rounded-md`     | 9px   |
| Cards            | `rounded-card`   | 12px  |
| Main panel       | `rounded-panel`  | 16px  |
| Dialogs          | `rounded-dialog` | 18px  |
| Pills/avatars    | `rounded-full`   | —     |

One radius per surface rank; do not round everything identically.

## Typography

| Rank      | Class / stack                          |
| --------- | -------------------------------------- |
| UI text   | `font-sans` → Inter, system fallback   |
| Data      | `font-mono` → Geist Mono, mono fallback |
| Heading   | semibold, tracking-tight               |
| Body      | regular                                |
| Label     | `text-xs`, muted, medium               |

## Shadows, blur, motion

- Shadows: soft black only (`shadow-panel`, `shadow-overlay`), never colored.
- Glass blur: `backdrop-blur-glass` (≈16px). Static; never animated.
- Motion durations: `motion-fast` 150ms, `motion-standard` 250ms,
  `motion-page` 350ms, `motion-slow` 400ms. Easing: `ease-out` for
  entrances, `ease-in-out` for persistent state. See
  [MOTION_GUIDELINES.md](MOTION_GUIDELINES.md).

## Rules

- Components must use semantic tokens; raw palette values appear only in
  `index.css` (and this document).
- Dark-only theme: `:root` holds the Obsidian values; `color-scheme: dark`.
- Token changes update this document and `index.css` together.
