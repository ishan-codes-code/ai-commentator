import WorkerStatus from "@/components/WorkerStatus.jsx";
import VideoImport from "@/components/VideoImport.jsx";
import GlassCard from "@/primitives/GlassCard.jsx";
import { Badge } from "@/components/ui/badge.jsx";

/**
 * Dashboard: video import + processing runs.
 * All run data comes from the real worker bridge; nothing is mocked.
 */
export default function DashboardSection() {
  return (
    <div className="flex flex-col gap-4">
      <VideoImport />
      <WorkerStatus />
      <GlassCard className="flex flex-col gap-1.5 p-5">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-[13px] font-semibold tracking-[-0.01em] text-foreground">
            Projects
          </h2>
          <Badge variant="outline">Planned</Badge>
        </div>
        <p className="text-[13px] text-muted-foreground">
          Create broadcast projects and reopen recent work here. Project
          management is a later milestone — nothing is stored yet.
        </p>
      </GlassCard>
    </div>
  );
}
