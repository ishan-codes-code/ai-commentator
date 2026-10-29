# ai-commentator

Docker-first Python app that analyses Free Fire gameplay footage with a
Baseten-hosted vision model. Details live in `docs/`; start with
`docs/current-state.md`.

## Commands

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
docker compose run --rm -e BASETEN_API_KEY="$BASETEN_API_KEY" \
  commentator python main.py --vision-batch --limit 5

# Offline unit tests (no API calls).
docker compose run --rm -v "$PWD/tests:/app/tests:ro" \
  commentator python -m unittest discover -s tests -v
```
