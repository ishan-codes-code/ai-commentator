# AI Commentator

A native desktop application that analyses recorded Free Fire gameplay
footage with a Baseten-hosted vision model and writes evidence-grounded
esports commentary. Details live in `docs/`; start with
`docs/current-state.md`.

## Architecture

```
Electron (React + Vite + Tailwind)  ←→  Python engine (OpenCV + Baseten)
   electron/src/main.js                    python/main.py
   electron/src/preload.js                  python/worker.py  (NDJSON over stdout)
   electron/src/renderer.jsx
```

- **Electron** owns the desktop shell: window, IPC routing, and the
  Python child-process lifecycle. It launches `python/worker.py` with
  `child_process.spawn()` and streams newline-delimited JSON progress to
  the renderer.
- **Python** owns the AI engine: frame extraction, vision analysis,
  structured observation validation, match-state reconstruction, event
  detection, commentary generation, and the final casting script.
- Python is a local processing engine, not a network server. There is no
  Docker, no FastAPI, no database in this project.
- The protocol between them is documented in
  `docs/contracts/worker-protocol.md`.

## Technology stack

| Layer | Choice |
|---|---|
| Desktop shell | Electron + React + Vite + Tailwind CSS |
| AI engine | Python (OpenCV, OpenAI-compatible client) |
| AI provider | Baseten (`meta-models/muse-spark-open-1026`) |
| Python env | Native `.venv` at the repository root |
| Electron ↔ Python | `spawn()` + NDJSON over stdout |

## Prerequisites

- Python 3.12+ (`python3 --version`)
- Node.js 20+ (`node --version`)
- A Baseten API key (paid stages only; see `.env.example`)

## Setup

```sh
# Python engine: native virtual environment at the repo root.
python3 -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows (PowerShell):
# .venv\Scripts\Activate.ps1
pip install -r requirements.txt

# API key for the paid vision/script stages (never commit .env).
cp .env.example .env
# Edit .env and set BASETEN_API_KEY.

# Desktop shell dependencies.
cd electron && npm install
```

## Running

```sh
# Desktop app (from electron/).
cd electron && npm start

# Python engine directly (from the repo root; defaults read
# gameplay/match.mp4 and write output/).
.venv/bin/python python/main.py --help
.venv/bin/python python/main.py --video /path/to/clip.mp4 --output-dir /tmp/run1
.venv/bin/python python/main.py --vision-test            # one paid request
.venv/bin/python python/main.py --vision-batch --limit 5 # paid, one request/frame
.venv/bin/python python/main.py --rebuild-state          # offline
.venv/bin/python python/main.py --detect-events          # offline
.venv/bin/python python/main.py --generate-commentary    # offline
.venv/bin/python python/main.py --commentary-write       # paid, opt-in
.venv/bin/python python/main.py --generate-script        # paid, opt-in

# Worker as Electron launches it (offline subset, no paid calls):
.venv/bin/python python/worker.py /path/to/clip.mp4 --output-dir /tmp/run1 --skip-vision
```

Output layout under the output directory: `frames/` holds extracted
JPEGs; `observations/` holds generated JSON (`frame_<timestamp>.json`
per analysed frame plus the `match_state.json` snapshot); `events/`
holds `events.json`; `commentary/` holds `commentary.json`; `script/`
holds `script.json` (the TTS input contract; TTS itself is not
implemented).

## How Electron communicates with Python

1. The renderer calls `window.api.processVideo(videoPath, options)`.
2. The main process validates the input, resolves the `.venv`
   interpreter and `python/worker.py`, and spawns it.
3. The worker emits `started` / `progress` / `stage_completed` /
   `result` / `error` JSON lines on stdout; diagnostics go to stderr.
4. The main process forwards them to the renderer as `worker-event`
   messages (`window.api.onWorkerEvent(...)`).
5. `window.api.cancelProcessing()` terminates the run; only one job runs
   at a time; the child is cleaned up when the app exits.

## Environment variables

| Name | Needed for |
|---|---|
| `BASETEN_API_KEY` | Paid stages: vision test/batch, commentary writer, script, and the worker's `vision_analysis`/`script` stages |

## Testing

```sh
# Python suite (offline, no API calls) — from the repo root.
.venv/bin/python -m unittest discover -s tests -v

# Electron protocol/process tests (dependency-free) — from electron/.
npm test

# Synthetic timing comparison (not collected by unittest).
.venv/bin/python tests/benchmark_jobs.py
```

## Current limitations

- The video-selection UI and dashboard are not built yet; the
  integration API and a minimal worker-status view exist.
- TTS/audio playback, live capture, and OBS integration are not
  implemented (see `docs/roadmap.md`).
- The `.venv` interpreter packaging/distribution problem for production
  builds is explicitly out of scope: the current target is local
  development with a native `.venv`.
- Live Baseten runs (vision batch, writer, script) are paid and opt-in;
  never run them without explicit authorization.
- Implemented versus planned: frame extraction, vision analysis,
  match-state tracking, event detection, deterministic and LLM-worded
  commentary, and the final casting script are implemented; see
  `docs/current-state.md` for the verified state.
