# Motion Guidelines

Restrained motion for the Obsidian AI shell. Motion serves orientation
(entrances, section switches, state feedback) — never decoration.
Tokens: durations below and in [DESIGN_TOKENS.md](DESIGN_TOKENS.md);
implementation constants in `electron/src/lib/tokens.js`.

## Library

- `motion/react` only. Never import `framer-motion` (it must not be
  installed alongside `motion`).
- One `MotionConfig reducedMotion="user"` at the root (`renderer.jsx`).
  Transform/layout animations switch off automatically for users who ask;
  opacity fades remain.
- Follow the installed Motion skill (`electron/.agents/skills/motion`):
  platform-specific best practices, `useTransform`/`useSpring` chains,
  `AnimatePresence` patterns.

## Tokens

| Token             | Value | Use                              |
| ----------------- | ----- | -------------------------------- |
| `motion-fast`     | 150ms | Hover, focus, micro-feedback     |
| `motion-standard` | 250ms | Entrances, popovers, toasts      |
| `motion-page`     | 350ms | Section transitions               |
| `motion-slow`     | 400ms | Large panel/dialog entrances     |

Easing: `easeOut` for entrances, `easeInOut` for state morphs.
Keep values in `lib/tokens.js`; do not scatter literals.

## Principles

1. **Fast and quiet.** Nothing on screen should take longer than 400ms
   to settle. Default to 150–250ms.
2. **Purposeful.** Animate to explain a change (section switch, status
   flip, panel open). If removal changes nothing about comprehension,
   remove it.
3. **Cheap properties only.** Opacity and transform. Never animate
   `backdrop-blur`, large blurred layers, box-shadow spread, or layout
   sizes except small `height: auto` disclosures (overflow hidden,
   `AnimatePresence`, small content only).
4. **One thing at a time.** Section transitions crossfade content; the
   sidebar indicator glides via a shared `layoutId`. No choreographed
   multi-element staggers on every navigation.
5. **No perpetual motion.** No looping glows, no animated gradients, no
   ambient drift. A processing indicator may pulse subtly (opacity only)
   and must stop on settle.

## Patterns

- **Section switch**: `AnimatePresence mode="wait"` + content fade/slide
  ≤ 350ms. Keep `AnimatePresence` mounted; key children stably.
- **Active nav indicator**: shared `layoutId` across nav items, spring or
  250ms ease — one instance per item (unique `layoutId` per repeated
  component).
- **Status change** (WorkerStatus): crossfade text, 150–250ms; processing
  dot pulses via opacity; error/done swap without layout jump.
- **Dialogs/popovers**: fade + 4–8px rise, ≤ 250ms.
- **Lists**: no per-row cascade on first paint; height-disclosure only
  for small expanders.

## Performance constraints

- Blur stays static. Any animation touching a glass surface animates
  opacity/transform of a wrapper, never the blur itself.
- Prefer `willChange` over `translateZ(0)` hacks; clean up after.
- Never read a `MotionValue` in render — read in effects/callbacks.
- Audit jank-prone additions with the Motion skill's performance-audit
  guidance before shipping them.

## Reduced motion

- `MotionConfig reducedMotion="user"` is mandatory and non-negotiable.
- Under reduced motion, every animation must degrade to an instant state
  change or an opacity-only fade — no slides, springs, or pulses.
- Verify by enabling the OS reduced-motion setting and walking the
  shell: navigation, status transitions, dialog open/close.

## Anti-patterns

Fade-up-on-everything scroll-ins, identical staggers per section,
hover lifts on cards, animated gradient borders, glowing pulsing CTAs,
and any `transition: all`.
