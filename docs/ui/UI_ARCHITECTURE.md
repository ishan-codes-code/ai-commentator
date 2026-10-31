# UI Architecture

Intended React renderer structure, layer boundaries, and the line between
what exists today and what is proposed. For the Electron/Python boundary,
see [../architecture.md](../architecture.md) and
[../contracts/worker-protocol.md](../contracts/worker-protocol.md).

## Current implementation (verified in repo)

- `electron/src/renderer.jsx` — React entry, renders `Hello` + `WorkerStatus`.
- `electron/src/components/Hello.jsx` — placeholder demo heading.
- `electron/src/components/WorkerStatus.jsx` — subscribes to
  `window.api.onWorkerEvent`, renders idle/processing/done/error states.
  This is the only renderer feature with backend behavior and must keep
  working unchanged in spirit (restyle allowed, mock state forbidden).
- `electron/src/index.css` — Tailwind v4 entry, `@theme` Obsidian tokens.
- `electron/src/components/ui/*` — shadcn primitives (button, card, badge,
  separator, skeleton). Generated code; edit only to fix integration bugs.
- `electron/src/lib/` — `cn` via the `cn` package; app-owned helpers
  (`tokens.js`, …) live here too.
- Path alias `@/*` → `electron/src/*` (Vite resolve + `jsconfig.json`).

## Proposed structure

```text
electron/src/
  renderer.jsx            # entry: providers (MotionConfig), App
  app/
    App.jsx               # shell composition + active-section state
    sections/             # Dashboard, Matches, Commentary, Settings
      DashboardSection.jsx
      MatchesSection.jsx
      CommentarySection.jsx
      SettingsSection.jsx
  layout/
    AppLayout.jsx         # sidebar + main panel frame
    Sidebar.jsx           # nav (Dashboard/Matches/Commentary/Settings)
    PageHeader.jsx        # title + description + actions row
  primitives/
    GlassPanel.jsx        # main glass surface
    GlassCard.jsx         # content card on canvas/surface
    StatusIndicator.jsx   # status dot + label (idle/processing/done/error)
  components/
    WorkerStatus.jsx      # worker bridge subscriber (existing behavior)
    ui/                   # generated shadcn components (do not hand-edit)
  lib/
    tokens.js             # motion durations, easings, shared constants
    worker-view.js        # pure mapping: worker message → view state (testable)
```

Feature-based sections own their content; `layout/` owns the frame;
`primitives/` owns cross-section surfaces; `components/ui/` owns generated
primitives. No section reaches past the layout layer into another section.

## Layers

1. **Entry/providers** (`renderer.jsx`): font CSS imports, `MotionConfig
   reducedMotion="user"`, error surface. No business logic.
2. **Layout** (`layout/`): window frame, sidebar nav, panel, header slots.
   Layout never calls `window.api` directly.
3. **Sections** (`app/sections/`): one placeholder-capable section per nav
   item. Sections compose primitives + shadcn components. Only sections
   that need live data subscribe to worker state (via `WorkerStatus`-style
   subscription or a shared hook extracted from it later).
4. **Primitives** (`primitives/` + `ui/`): stateless visuals. Props in,
   markup out. No IPC, no timers, no fetches.

## Library boundaries

- **React 19 + JSX.** No TypeScript migration (shadcn configured with
  `tsx: false`; `jsconfig.json` for paths).
- **Tailwind CSS v4**, CSS-first tokens in `index.css`. No
  `tailwind.config.js`.
- **shadcn (radix default registry)**: compose generated components first;
  custom markup only where no component exists. Generated `ui/` files stay
  pristine; project styling goes through tokens and wrapper primitives.
- **Motion** via `motion/react` only (never `framer-motion`). See
  [MOTION_GUIDELINES.md](MOTION_GUIDELINES.md).
- **lucide-react** for icons (props, never emoji; `data-icon` in buttons).
- **No Zustand** in this milestone (local `useState` suffices). No router:
  section state is a single `activeSection` value in `App`.

## State and data rules

- The renderer is a thin client over `window.api` (`processVideo`,
  `cancelProcessing`, `onWorkerEvent`). No other backend surface exists.
- Never invent worker data: unimplemented sections render honest empty
  states, never fake pipelines or mocked processing.
- Event handling follows the existing `WorkerStatus` pattern (subscribe in
  `useEffect`, unsubscribe on cleanup); extract a shared hook only when a
  second consumer needs it.

## What changes next (not now)

Video-selection flow, dashboard data wiring, and any store/router adoption
are staged in [UI_ROADMAP.md](UI_ROADMAP.md).
