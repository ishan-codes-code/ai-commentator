---
name: docker-verify
description: Build, run, and troubleshoot this repo's Docker-first workflow without changing app files.
---

# Docker Verify

Use when building, running, or troubleshooting the containerized app.

- Runtime is Docker-first: `docker compose build`, then
  `docker compose run --rm commentator` (see `docs/testing.md`).
- The container runs as non-root; `./gameplay` is mounted read-only and
  `./output` read-write. Do not change mounts, users, or
  `network_mode: bridge` to bypass an error — diagnose first and record
  evidence in your report.
- A successful build does not prove API connectivity; report build, run,
  and connectivity results separately.
- Leave `Dockerfile` and `compose.yaml` untouched unless the task
  explicitly authorizes changing them.
