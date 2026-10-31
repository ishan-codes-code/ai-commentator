# Obsidian Studio — Design Tokens

Single source of truth for token **values and mappings**. Design intent
lives in [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md); usage rules in
[COMPONENT_GUIDELINES.md](COMPONENT_GUIDELINES.md).

Implementation: CSS custom properties + Tailwind CSS v4 `@theme` in
`electron/src/index.css`. Components reference semantic utilities only
(`bg-background`, `text-foreground`, `border-border`, …).

## Raw palette

| Token name      | Value                    | Role                              |
| --------------- | ------------------------ | --------------------------------- |
| `studio-950`    | `#030304`                | Application canvas                |
| `studio-900`    | `#08080A`                | Primary surface                   |
| `studio-850`    | `#0C0C10`                | Raised surface                    |
| `studio-800`    | `#121218`                | Elevated surface                  |
| `violet`        | `#9670FF`                | Primary accent (restrained)       |
| `ice`           | `#38BDF8`                | Secondary / technical signal      |
| `success`       | `#22C55E`                | Success status                    |
| `warning`       | `#F59E0B`                | Warning status                    |
| `danger`        | `#EF4444`                | Danger status                     |
| `ink`           | `#F5F5F7`                | Primary text                      |
| `ink-muted`     | `#92929D`                | Secondary text                    |
| `ink-subtle`    | `#686873`                | Metadata only (never body copy)   |
| `line`          | `#1C1C24`                | Solid hairline border             |

Glass (overlays only): fill `rgba(12,12,16,0.82)`, border `#262630`,
blur ≈ 16px.

## Semantic mapping

| Semantic token        | Tailwind utility          | Value source              |
| --------------------- | ------------------------- | ------------------------- |
| `background`          | `bg-background`           | `studio-950`              |
| `foreground`          | `text-foreground`         | `ink`                     |
| `card` / `card-fore.` | `bg-card` / `text-card…`  | `studio-900` / ink        |
| `popover`             | `bg-popover`              | `studio-850`              |
| `primary`             | `bg-primary`              | `violet`                  |
| `primary-foreground`  | `text-primary-foreground` | `ink`                     |
| `secondary`           | `text-secondary` (signal) | `ice`                     |
| `muted`               | `bg-muted` / `text-muted…`| `studio-800` / ink-muted  |
| `subtle`              | `text-subtle`             | `ink-subtle` (metadata)   |
| `accent`              | `bg-accent`               | `studio-800`              |
| `border` / `input`    | `border-border`           | `line` (solid)            |
| `ring`                | `focus-visible:ring-ring` | `violet`                  |
| `success` / `warning` / `destructive` | status only | palette            |
| `surface`             | `bg-surface`              | `studio-900` (sidebar)    |
| `surface-raised`      | `bg-surface-raised`       | `studio-850` (panels)     |
| `elevated`            | `bg-elevated`             | `studio-800`              |
| `glass`               | (overlays only)           | glass fill                |

Status colors are used for status only. Muted text never carries primary
actions; subtle text never carries content.

## Spacing

Base unit **4px** (Tailwind default scale). Named applications:

| Use                 | Token / class | Value |
| ------------------- | ------------- | ----- |
| View padding        | `px-6 py-6`   | 24px  |
| Header height       | `h-12`        | 48px  |
| Sidebar width       | `w-60`        | 240px |
| Card padding (std)  | `p-5`         | 20px  |
| Control gaps        | `gap-2`/`gap-4` | 8/16px |

Use the scale (`gap-*`, `p-*`), never ad-hoc pixel values.

## Radius

| Surface          | Token / class    | Value |
| ---------------- | ---------------- | ----- |
| Base (`--radius`)| `rounded-md`     | 8px   |
| Cards            | `rounded-card`   | 10px  |
| Panels           | `rounded-panel`  | 12px  |
| Dialogs          | `rounded-dialog` | 14px  |

One radius per surface rank; 8px is the control default.

## Typography

| Rank      | Size / class                        |
| --------- | ----------------------------------- |
| View title (header bar) | 13px semibold, `-0.01em`   |
| Section title | 15px semibold, `-0.01em`          |
| UI body / labels | 13px regular (`body` default) |
| Metadata / mono detail | 12px, `text-subtle`/`text-muted-foreground` |
| Micro-labels (sidebar footer) | 11px, `text-subtle` |
| Data      | `font-mono` → Geist Mono            |

Body default is 13px/1.5; headings use `leading-tight`.

## Shadows, blur, motion

- Shadows are near-invisible structure (`shadow-panel`: 1px, 50% black;
  `shadow-overlay` for modals only), never colored.
- Glass blur (`backdrop-blur-glass` ≈ 16px) is reserved for dialogs and
  popovers. Layout never blurs.
- Motion durations: `motion-fast` 150ms, `motion-standard` 250ms,
  `motion-page` 350ms, `motion-slow` 400ms. Section transitions are
  opacity-only. See [MOTION_GUIDELINES.md](MOTION_GUIDELINES.md).

## Rules

- Components use semantic tokens; raw palette values appear only in
  `index.css` (and this document).
- Dark-only theme: `:root` holds the Studio values; `color-scheme: dark`.
- Token changes update this document and `index.css` together.
