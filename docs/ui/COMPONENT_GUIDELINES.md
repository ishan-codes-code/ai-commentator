# Component Guidelines

Rules for building renderer UI with shadcn primitives, project
primitives, and composition. Token values: [DESIGN_TOKENS.md](DESIGN_TOKENS.md).
Structure: [UI_ARCHITECTURE.md](UI_ARCHITECTURE.md).

## shadcn first

- Compose generated components before writing custom markup:
  settings page = Tabs + Card + form controls; dashboard = Sidebar +
  Card + Badge + Table.
- Check what exists with `npx shadcn@latest search` (and
  `npx shadcn@latest docs <component>`) before adding new components.
- Generated `src/components/ui/*` files stay pristine; fix integration
  issues by adjusting tokens or wrappers, not by forking primitives.
- New components land via `npx shadcn@latest add <name>` (JS output,
  `tsx: false` in `electron/components.json`).

## Variants before custom styles

- Use built-in variants: `variant="outline"`, `size="sm"`, etc.
- Use semantic color utilities: `bg-primary`, `text-muted-foreground`,
  `border-border`. Never raw values (`bg-blue-500`) or ad-hoc hex.
- Extend via `cn()` (the `cn` package), never manual ternaries for
  conditional classes.

## Composition rules (always enforced)

- `className` is for layout (`flex`, `gap-*`, `p-*`), not for
  restyling a component's colors or type.
- Vertical stacks: `flex flex-col gap-*`. No `space-x-*` / `space-y-*`.
- Square sizing: `size-*` (`size-9`, not `w-9 h-9`).
- Truncation: `truncate`, not manual ellipsis chains.
- No manual `dark:` overrides — the app is dark-only via tokens.
- No manual `z-index` on overlays (Dialog/Sheet/Popover manage stacking).
- Items always inside their Group (`SelectItem` → `SelectGroup`, …).
- Triggers via `asChild` + a `motion` child where animation is needed.
- Dialogs/Sheets always include a Title (visually hidden with `sr-only`
  if the design has none) for accessibility.
- Full Card composition: `CardHeader`/`CardTitle`/`CardDescription`/
  `CardContent`/`CardFooter` — never a bare content dump.
- Buttons have no `isPending`: compose `Spinner` + `disabled`.
- `Avatar` always pairs with `AvatarFallback`.
- `Separator`, not `<hr>`; `Skeleton` for loading, no custom pulse divs;
  `Badge` for status chips, no custom spans; `Alert` for callouts;
  `Empty` for empty states (compose with project copy, see below).

## Icons

- Lucide only (`lucide-react`), passed as objects, never emoji.
- In `Button`: `data-icon="inline-start"|"inline-end"`, no sizing classes
  (components size their own icons).

## Forms (when the video-selection flow lands)

- Layout with `FieldGroup` + `Field`, never raw div grids.
- `InputGroup` owns `InputGroupInput`/`Textarea` + `InputGroupAddon`.
- Option sets (2–7 choices) use `ToggleGroup`, not mapped buttons.
- Validation: `data-invalid` on `Field`, `aria-invalid` on the control.

## States

- **Loading**: `Skeleton` shapes matching the eventual layout; spinners
  only for button-pending or indeterminate waits.
- **Empty**: honest empty states (icon + title + one-line why + next
  action or disabled-action explanation). Never fake data, never a
  spinner that never resolves.
- **Error**: worker errors surface `stage` + human message (the
  `WorkerStatus` pattern); no secret leakage, no raw stack traces.
- Every async surface needs all three: loading, empty, error.

## Reusable boundaries

- `primitives/` (GlassCard, StatusIndicator):
  stateless, token-driven, no IPC. New shared visuals go here only after
  a second use-site appears — one-offs stay in their section.
- `layout/` (AppLayout, AppHeader, Sidebar): frame only; the header
  status pill reads through `hooks/useWorkerStatus`, never raw IPC.
- Type scale: 15px semibold section titles, 13px UI body, 12px metadata,
  11px micro-labels; tight tracking on headings, no hero sizes.
- Do not duplicate: search `primitives/` and `ui/` before creating a
  variant; similar-looking custom divs in two sections are a merge
  candidate, not a pattern.

## Accessibility baseline

- Interactive elements are real buttons/inputs (keyboard reachable).
- Focus visible via the `ring` token; never remove outlines without a
  replacement.
- Status is text + icon, never color alone.
- Respect `prefers-reduced-motion` (see [MOTION_GUIDELINES.md](MOTION_GUIDELINES.md)).
