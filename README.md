# ai-commentator

Docker-first Python app that analyses Free Fire gameplay footage with a
Baseten-hosted vision model. Details live in `docs/`; start with
`docs/current-state.md`.

## Commands

Output layout: `output/frames/` holds extracted JPEGs;
`output/observations/` holds generated JSON (`frame_<timestamp>.json`
per analysed frame plus the `match_state.json` snapshot);
`output/events/` holds the generated `events.json` snapshot;
`output/commentary/` holds the generated `commentary.json` snapshot.

```sh
# Build the image.
docker compose build

# Extract frames from gameplay/match.mp4 into output/frames/.
docker compose run --rm commentator

# Analyse the first frame only (single paid request).
docker compose run --rm -e BASETEN_API_KEY="$BASETEN_API_KEY" \
  commentator python main.py --vision-test

# Analyse up to 5 frames spread across the clip (paid, one request
# per analysed frame). Omit --limit to use the default of 5.
# Append --jobs J (1-4, default 1) to bound concurrent vision requests.
docker compose run --rm -e BASETEN_API_KEY="$BASETEN_API_KEY" \
  commentator python main.py --vision-batch --limit 5 --jobs 2

# Move legacy observation JSONs from output/frames/ to
# output/observations/ (offline, verified, idempotent).
docker compose run --rm commentator python main.py --migrate-observations

# Rebuild output/observations/match_state.json from stored
# observations (offline, no API calls).
docker compose run --rm commentator python main.py --rebuild-state

# Detect evidence-grounded events into output/events/events.json
# (offline, no API calls).
docker compose run --rm commentator python main.py --detect-events

# Generate deterministic commentary into
# output/commentary/commentary.json (offline, no API calls).
docker compose run --rm commentator python main.py --generate-commentary

# Word gated claims with one text-only model call (paid, opt-in).
docker compose run --rm -e BASETEN_API_KEY="$BASETEN_API_KEY" \
  commentator python main.py --commentary-write

# Generate evidence-grounded commentary into
# output/commentary/commentary.json (offline, no API calls).
docker compose run --rm commentator python main.py --generate-commentary

# Offline unit tests (no API calls).
docker compose run --rm -v "$PWD/tests:/app/tests:ro" \
  commentator python -m unittest discover -s tests -v
```
