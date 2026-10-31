// Shared UI constants. Durations are seconds for motion/react;
// the same values are documented as milliseconds in docs/ui/.
export const MOTION = {
  fast: 0.15,
  standard: 0.25,
  page: 0.35,
  slow: 0.4,
};

export const EASING = {
  entrance: "easeOut",
  state: "easeInOut",
};

export const SECTIONS = [
  { id: "dashboard", label: "Dashboard" },
  { id: "matches", label: "Matches" },
  { id: "commentary", label: "Commentary" },
  { id: "settings", label: "Settings" },
];
