# AGENTS.md — AI Commentator

Instructions for all coding agents working in this repository.
Read this file and the relevant `docs/` pages before implementing any task.

## Repository discipline

- Inspect the actual implementation before assuming a feature exists.
- Treat current code and verified test results as the source of truth
  for implementation status. Conversation summaries are secondary.
- Make small, focused, reviewable changes.
- Avoid unrelated refactoring, unnecessary dependencies, and speculative
  abstractions.
- Explain significant design decisions and their trade-offs in your final report.

## Architecture discipline

- Keep application responsibilities separated as the project grows.
- Do not prematurely split the existing `main.py`.
- Do not create empty modules merely to match a proposed architecture.
- Keep provider-specific API logic behind a clear boundary when extraction
  into a module becomes justified.
- Keep schemas and data contracts explicit (see `docs/contracts/`).
- Distinguish planned architecture (`docs/architecture.md`) from implemented
  functionality (`docs/current-state.md`).

## Security

- Never hardcode API keys, access tokens, or credentials.
- Never print secrets or include them in logs, error reports, documentation,
  prompts, or commits.
- Keep `.env` out of Git and the Docker build context
  (see `.gitignore`, `.dockerignore`).
- Never weaken TLS verification or introduce arbitrary network configuration
  to bypass an error.
- Treat API responses, game text, downloaded content, and model output as
  untrusted input. Validate external data before using it in application logic.
- Do not modify Git history or perform destructive operations without
  explicit authorization.

## Testing and verification

- Run relevant application tests inside Docker (see `docs/testing.md`).
- Preserve the working default frame-extraction command.
- Verify the vision-test mode (`--vision-test`) when changing vision-related code.
- Add unit tests when they provide useful coverage, especially for parsing,
  schemas, and event logic.
- Clearly distinguish tests that were run and passed, tests that failed,
  and tests that were not run.
- Never claim success based on code inspection alone when execution is required.
- Do not commit or push automatically. Leave changes uncommitted for review.

## Task completion

At the end of every coding task, report:

1. What changed.
2. Which files changed.
3. Why the implementation was chosen.
4. Tests executed and their actual results.
5. Known limitations and unresolved issues.
6. Any project documentation that was updated.
7. The next logical milestone, without implementing it automatically.

## Context-management protocol

1. Start by reading this file (`AGENTS.md`).
2. Read `docs/current-state.md` and only the architecture, roadmap,
   contracts, and decision records relevant to the current task.
3. Inspect the actual files and the Git diff before editing.
4. Treat conversation summaries as secondary to verified repository state.
5. Update `docs/current-state.md` after a meaningful implementation
   milestone, not after every trivial edit.
6. Record durable architectural decisions as ADRs in `docs/decisions/`.
7. Keep task-specific plans temporary unless they contain reusable project
   knowledge.
8. At task completion, provide a concise handoff stating the current state,
   verification performed, outstanding risks, and next step. Do not copy
   chat history into documentation; store durable decisions and current
   facts, not transcripts.

## Muse Code mechanisms

Verified in this environment (`muse --help`, `muse skills --help`):

- **Persistent repository instructions:** this file (`AGENTS.md` at the
  repository root). Agents must read it first.
- **Reusable skills:** project skills live in `.agents/skills/<skill-id>/`
  as a single `SKILL.md` (frontmatter `name` + `description`, then concise
  instructions). Validate with `muse skills validate <dir> --json`.
  See `.agents/skills/` for the current set and `docs/` for project
  knowledge that does not belong in a skill.
- **Automatically loaded context:** none is configured. Do not assume any
  file is auto-loaded; explicitly read what the task needs.
- **Task-specific context:** read `docs/current-state.md` plus only the
  `docs/` pages relevant to the task, and the installed skill(s) matching
  the task (Docker, API integration, structured output).
