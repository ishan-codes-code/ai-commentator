import GlassCard from "@/primitives/GlassCard.jsx";

/**
 * Placeholder until Stage 2 (dashboard data).
 * No invented matches.
 */
export default function MatchesSection() {
  return (
    <div className="flex flex-col gap-4">
      <GlassCard className="flex flex-col gap-1.5 p-5">
        <h2 className="text-[13px] font-semibold tracking-[-0.01em] text-foreground">
          No matches yet
        </h2>
        <p className="text-[13px] text-muted-foreground">
          Matches will appear here once videos can be selected and
          processed. No demo data is shown in the meantime.
        </p>
      </GlassCard>
    </div>
  );
}
