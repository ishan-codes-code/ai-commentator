export const DEFAULT_SETTINGS = Object.freeze({
  version: 1,
  provider: { type: 'openai-compatible', baseUrl: 'https://inference.baseten.co/v1', model: 'meta-models/muse-spark-open-1026', modelVisionVerified: false },
  game: { id: 'free-fire', context: '' },
  prompt: { personality: 'Esports commentator', tone: 'energetic', language: 'English', instructions: '' },
  onboardingCompleted: false,
});
