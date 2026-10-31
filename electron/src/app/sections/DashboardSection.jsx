import PageHeader from "@/layout/PageHeader.jsx";
import WorkerStatus from "@/components/WorkerStatus.jsx";
import VideoImport from "@/components/VideoImport.jsx";

/**
 * Dashboard: video import + processing runs.
 * All run data comes from the real worker bridge; nothing is mocked.
 */
export default function DashboardSection() {
  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Dashboard"
        description="Processing status and recent commentary runs."
      />
      <VideoImport />
      <WorkerStatus />
    </div>
  );
}
