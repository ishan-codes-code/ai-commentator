# 0001 — Docker-first development

- **Status:** accepted.

## Context

The project must run reproducibly in GitHub Codespaces and later in
deployment environments that differ from any single developer machine.
Python/OpenCV environments drift easily across hosts.

## Decision

The application executes in Docker. The documented runtime and integration
tests use Docker (`docker compose build`, `docker compose run`). Host-side
editor tooling (linting, editing, Git) is permitted, but anything that
defines runtime behavior must be verified inside the container.

## Alternatives considered

- **Host-only Python:** simpler for quick edits, but reproduces
  environment drift; rejected as the documented runtime.
- **Mixed host/container without a rule:** lets each contributor choose;
  rejected because verification results would not be comparable.

## Consequences

- Reproducible builds and comparable test results across machines.
- Contributors need Docker available; container startup adds latency.
- The current `network_mode: bridge` setting in `compose.yaml` is an
  implementation detail of this decision, not a universal recommendation.
  Any future networking change must be based on diagnosis and documented
  evidence — never an arbitrary bypass added to silence an error.
