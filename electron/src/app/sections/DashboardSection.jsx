import { Clapperboard } from "lucide-react";
import PageHeader from "@/layout/PageHeader.jsx";
import GlassCard from "@/primitives/GlassCard.jsx";
import WorkerStatus from "@/components/WorkerStatus.jsx";
import { Badge } from "@/components/ui/badge.jsx";

/**
 * Dashboard: honest empty state until the video-selection flow
 * (UI_ROADMAP Stage 1) produces real runs. No mocked data.
 */
export default function DashboardSection() {
  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Dashboard"
        description="Processing status and recent commentary runs."
      />
      <GlassCard className="flex flex-col items-center gap-3 p-8 text-center">
        <span className="flex size-11 items-center justify-center rounded-full bg-accent text-muted-foreground">
          <Clapperboard className="size-5" aria-hidden />
        </span>
        <h2 className="text-base font-semibold text-foreground">
          No runs yet
        </h2>
        <p className="max-w-sm text-sm text-muted-foreground">
          Select a match video to start frame extraction and commentary.
          Video selection arrives with the next milestone.
        </p>
        <div className="flex items-center gap-2">
          <Badge variant="secondary">Idle</Badge>
          <Badge variant="outline">0 runs</Badge>
        </div>
      </GlassCard>
      <WorkerStatus />
    </div>
  );
}
