import GlassCard from "@/primitives/GlassCard.jsx";

/**
 * Placeholder for app preferences. Engine and provider configuration
 * stay outside the renderer (see docs/architecture.md boundaries).
 */
export default function SettingsSection() {
  return (
    <div className="flex flex-col gap-4">
      <GlassCard className="flex flex-col gap-1.5 p-5">
        <h2 className="text-[13px] font-semibold tracking-[-0.01em] text-foreground">
          No settings yet
        </h2>
        <p className="text-[13px] text-muted-foreground">
          Renderer preferences will live here. Engine, worker, and
          provider settings are intentionally not exposed to the UI.
        </p>
      </GlassCard>
    </div>
  );
}
