# Application settings v1

Global, non-secret user preferences are stored as `settings.json` under
Electron `app.getPath('userData')`; project metadata remains independent.
Electron validates the document and atomically replaces it on save. Unknown
game/provider values and invalid URLs are rejected. Missing files use
defaults; partially populated documents are defaulted before validation. A
malformed document loads defaults without overwriting the original file.

```json
{
  "version": 1,
  "provider": {"type":"openai-compatible","baseUrl":"https://inference.baseten.co/v1","model":"meta-models/muse-spark-open-1026","modelVisionVerified":false},
  "game": {"id":"free-fire","context":""},
  "prompt": {"personality":"Esports commentator","tone":"energetic","language":"English","instructions":""},
  "onboardingCompleted": false
}
```

Credentials never appear in this document. Electron stores ciphertext in
`provider-key.enc` using `safeStorage`; unavailable secure storage rejects
credential writes and reads. Preload returns only key-presence and secure
storage availability metadata.

For engine runs, Electron passes provider base URL/model and serialized
non-secret preferences through `BASETEN_BASE_URL`, `BASETEN_MODEL`, and
`AI_COMMENTATOR_SETTINGS_JSON`. A decrypted saved key is passed only through
the worker child environment as `BASETEN_API_KEY`. The legacy process
environment key remains accepted for existing development installations.

Provider setup operations run `python/provider_setup.py` from Electron's main
process. Connection and model discovery use the OpenAI-compatible models
endpoint. Every discovered model remains marked unverified until the user
explicitly runs `verify-vision`, which sends a small image request and marks
the selected model verified only after success. That request may be billable.
Provider exception text is never forwarded.

Prompt preferences are untrusted style input. They do not replace the fixed
`observation-v1` format, evidence gates, uncertainty rules, or deterministic
commentary/script validation.
