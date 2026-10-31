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
| `studio-950`    | `#09090A`                | Application canvas                |
| `studio-900`    | `#101011`                | Primary surface                   |
| `studio-850`    | `#171718`                | Raised surface                    |
| `studio-800`    | `#202021`                | Elevated surface                  |
| `success`       | `#22C55E`                | Success status                    |
| `warning`       | `#F59E0B`                | Warning status                    |
| `danger`        | `#EF4444`                | Danger status                     |
| `ink`           | `#F1F1F1`                | Primary text                      |
| `ink-muted`     | `#A1A1A4`                | Secondary text                    |
| `ink-subtle`    | `#77777B`                | Metadata                          |
| `line`          | `#2A2A2B`                | Solid hairline border             |

Overlay fill `rgba(23,23,24,0.96)`, border `#303031`; blur is not used
for layout surfaces.

## Semantic mapping

| Semantic token        | Tailwind utility          | Value source              |
| --------------------- | ------------------------- | ------------------------- |
| `background`          | `bg-background`           | `studio-950`              |
| `foreground`          | `text-foreground`         | `ink`                     |
| `card` / `card-fore.` | `bg-card` / `text-card…`  | `studio-900` / ink        |
| `popover`             | `bg-popover`              | `studio-850`              |
| `primary`             | `bg-primary`              | `#EDEDED` (monochrome action) |
| `primary-foreground`  | `text-primary-foreground` | `#111112`                     |
| `secondary`           | `bg-secondary`            | Neutral raised surface        |
| `muted`               | `bg-muted` / `text-muted…`| `studio-800` / ink-muted  |
| `subtle`              | `text-subtle`             | `ink-subtle` (metadata)   |
| `accent`              | `bg-accent`               | `studio-800`              |
| `border` / `input`    | `border-border`           | `line` (solid)            |
| `ring`                | `focus-visible:ring-ring` | `#B9B9BB`                  |
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
| View padding        | `px-5 py-4`   | 20/16px |
| Header height       | `h-11`        | 44px  |
| Sidebar width       | `w-[196px]`   | 196px |
| Card padding (std)  | `p-4`         | 16px  |
| Control gaps        | `gap-2`/`gap-4` | 8/16px |

Use the scale (`gap-*`, `p-*`), never ad-hoc pixel values.

## Radius

| Surface          | Token / class    | Value |
| ---------------- | ---------------- | ----- |
| Base (`--radius`)| `rounded-md`     | 6px   |
| Cards            | `rounded-card`   | 8px   |
| Panels           | `rounded-panel`  | 10px  |
| Dialogs          | `rounded-dialog` | 12px  |

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
- Any future overlay blur must remain static and limited to dialogs and
  popovers; layout never blurs.
- Motion durations: `motion-fast` 150ms, `motion-standard` 250ms,
  `motion-page` 350ms, `motion-slow` 400ms. Section transitions are
  opacity-only. See [MOTION_GUIDELINES.md](MOTION_GUIDELINES.md).

## Rules

- Components use semantic tokens; raw palette values appear only in
  `index.css` (and this document).
- Dark-only theme: `:root` holds the Studio values; `color-scheme: dark`.
- Token changes update this document and `index.css` together.
