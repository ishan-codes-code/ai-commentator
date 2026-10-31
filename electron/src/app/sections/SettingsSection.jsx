import PageHeader from "@/layout/PageHeader.jsx";
import GlassCard from "@/primitives/GlassCard.jsx";

/**
 * Placeholder for app preferences. Engine and provider configuration
 * stay outside the renderer (see docs/architecture.md boundaries).
 */
export default function SettingsSection() {
  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Settings"
        description="Application preferences."
      />
      <GlassCard className="p-6">
        <h2 className="text-sm font-semibold text-foreground">
          Nothing here yet
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Renderer preferences will live here. Engine, worker, and
          provider settings are intentionally not exposed to the UI.
        </p>
      </GlassCard>
    </div>
  );
}
