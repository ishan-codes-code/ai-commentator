# ADR 0007: Global settings and provider credential boundary

- Status: Accepted
- Date: 2026-11-01

## Context

M5 needs global provider, game, and prompt preferences while preserving
project-scoped metadata and preventing API keys from entering the renderer or
normal settings files. The local Python worker reserves stdin for the
versioned cooperative stop command.

## Decision

Store validated non-secret preferences in a separate atomically written
`settings.json` under Electron `userData`. Store the API key separately as
Electron `safeStorage` ciphertext, with no plaintext fallback. Main-process
IPC exposes only key-presence and secure-storage availability metadata.
Decrypt immediately before a Python child starts and pass the key in the
child environment. Keep the stdin stop protocol unchanged.

Provider setup stays in Python via a dedicated one-shot entry point using the
existing OpenAI-compatible SDK. Discovery never implies vision capability; a
model must pass an explicit image-input check before saved-key vision runs
are ready. Custom prompt preferences are style instructions only and never
disable deterministic validation.

## Consequences

The first release supports one OpenAI-compatible endpoint type. The image
capability check is user-initiated and may incur a provider charge. Platforms
without Electron secure-storage availability cannot save API keys. The child
environment is the controlled credential transport; secrets do not travel
through argv, worker messages, or stdin.
