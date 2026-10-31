import PageHeader from "@/layout/PageHeader.jsx";
import GlassCard from "@/primitives/GlassCard.jsx";

/**
 * Placeholder until UI_ROADMAP Stage 2 (dashboard data).
 * No invented matches.
 */
export default function MatchesSection() {
  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Matches"
        description="Recorded matches and their processing runs."
      />
      <GlassCard className="p-6">
        <h2 className="text-sm font-semibold text-foreground">
          Nothing here yet
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Matches will appear here once videos can be selected and
          processed. No demo data is shown in the meantime.
        </p>
      </GlassCard>
    </div>
  );
}
