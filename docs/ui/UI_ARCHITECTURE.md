# UI Architecture

Intended React renderer structure, layer boundaries, and the line between
what exists today and what is proposed. For the Electron/Python boundary,
see [../architecture.md](../architecture.md) and
[../contracts/worker-protocol.md](../contracts/worker-protocol.md).

## Current implementation (verified in repo)

- `electron/src/renderer.jsx` — React entry, renders `App` (fonts + `MotionConfig reducedMotion="user"`).
- `electron/src/app/` — `App.jsx` shell with section state; `sections/`
  includes the project library, project workspace, analysis/commentary
  views, settings, and honest prompt/audio placeholders.
- `electron/src/layout/` — `AppLayout`, `Sidebar`, and `AppHeader`;
  navigation collapse is controlled by `App`, while inspector visibility is
  controlled at the workspace boundary and exposed in the header.
- `electron/src/app/sections/workspace/` — `ProjectWorkspaceLayout` owns the
  fixed editor grid and dock; `EditorCenter` owns preview/artifact tabs;
  `ProcessingInspector` presents existing run state and project information;
  `DockedWorkspace` hosts bottom-panel tabs (Audio only today).
- `electron/src/primitives/` — `GlassPanel`, `GlassCard`, `StatusIndicator`.
- `electron/src/app/sections/DashboardSection.jsx` — project creation,
  listing, opening, and deletion through the audited `window.api` bridge.
- `electron/src/app/sections/WorkspaceSection.jsx` — project loading,
  project-bound processing and its single worker subscription; it composes
  the workspace regions.
- `electron/src/components/RunPanel.jsx` — presentational run states
  driven by `lib/worker-view.js`.
- `electron/src/components/WorkerStatus.jsx` — dashboard worker status;
  `AppHeader` also reads live status through `useWorkerStatus`. Worker
  state comes only from real events, never mock state.
- `electron/src/index.css` — Tailwind v4 entry, `@theme` monochrome studio
  tokens.
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
    sections/             # projects Dashboard + Workspace, stubs
      DashboardSection.jsx  # Your Projects + Add Project flow
      WorkspaceSection.jsx  # per-project workspace (preview/lanes/runs)
      MatchesSection.jsx
      CommentarySection.jsx
      SettingsSection.jsx
  layout/
    AppLayout.jsx         # sidebar + header + content column (flat)
    Sidebar.jsx           # nav (Dashboard/Matches/Commentary/Settings)
    AppHeader.jsx         # slim bar: section title + engine status
  primitives/
    GlassCard.jsx         # solid content card on canvas/surface
    StatusIndicator.jsx   # status dot + label (5 run states)
  hooks/
    useWorkerStatus.js    # shared worker subscription (header + card)
  components/
    WorkerStatus.jsx      # worker bridge subscriber (existing behavior)
    RunPanel.jsx          # presentational run states (progress/result/error)
    ui/                   # generated shadcn components (do not hand-edit)
  lib/
    tokens.js             # motion durations, easings, shared constants
    worker-view.js        # pure mapping: worker message → view state (testable)
    format.js             # pure display helpers (bytes, names, file URLs)
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
   subscription via the shared `hooks/useWorkerStatus` hook).
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

- The renderer is a thin client over `window.api` (`processVideo`
  with optional `projectId` binding, `cancelProcessing`, `onWorkerEvent`,
  `selectVideo`, plus project CRUD: `list-projects`, `create-project`,
  `open-project`, `update-project`, `delete-project`, `list-artifacts`,
  `read-artifact`). Media renders over the privileged `project://`
  scheme (validated main-side, bare 404s).
  Projects live under Electron `userData` (see `project-store.mjs`);
  All paths, validation, and atomic writes stay main-side — no
  filesystem access reaches the renderer. No other backend surface exists.
- Project workspaces are fixed to the Electron viewport. Scroll stays within
  the inspector, artifact view, or global library section. The dock uses a
  fixed height because no panel-resizing dependency is installed.
- Never invent worker data: unimplemented sections render honest empty
  states, never fake pipelines or mocked processing.
- Event handling uses the shared `useWorkerStatus` hook (subscribe in
  `useEffect`, unsubscribe on cleanup); extract a shared hook only when a
  second consumer (`AppHeader` pill + `WorkerStatus` card).

## What changes next (not now)

Stage 1 delivered video selection and run wiring (this document's
Dashboard no longer a stub). Remaining:

Artifact browsing, and any store/router adoption
are staged in [UI_ROADMAP.md](UI_ROADMAP.md).
