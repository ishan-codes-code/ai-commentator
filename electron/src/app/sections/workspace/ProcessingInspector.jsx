import { FolderOpen, Play, Sparkles } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert.jsx";
import { Badge } from "@/components/ui/badge.jsx";
import { Button } from "@/components/ui/button.jsx";
import { Separator } from "@/components/ui/separator.jsx";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs.jsx";
import RunPanel from "@/components/RunPanel.jsx";
import StatusIndicator from "@/primitives/StatusIndicator.jsx";
import { extensionOf, fileNameOf, formatBytes } from "@/lib/format.js";
import { RUN_MODES, VISION_FRAME_LIMIT, isAiMode } from "@/lib/run-options.js";
import { stageLabel } from "@/lib/worker-view.js";

const RUN_STATUS = {
  idle: { key: "idle", label: "Idle" },
  processing: { key: "processing", label: "Processing" },
  completed: { key: "done", label: "Completed" },
  failed: { key: "error", label: "Failed" },
  cancelled: { key: "cancelled", label: "Cancelled" },
};

const PROJECT_STATUS = {
  idle: "Idle",
  processing: "Processing",
  done: "Completed",
  error: "Failed",
  cancelled: "Cancelled",
  interrupted: "Interrupted",
};

function SectionHeading({ children, id }) {
  return <h2 id={id} className="text-[11px] font-medium text-muted-foreground">{children}</h2>;
}

function ProcessingContent({ project, run, canStart, starting, cancelling, cancelError, onStart, onCancel }) {
  const interrupted = project.processingStatus === "interrupted" && run.status === "idle";
  const displayStatus = interrupted
    ? { key: "interrupted", label: "Interrupted" }
    : RUN_STATUS[run.status] ?? { key: "idle", label: "Idle" };

  return (
    <div className="flex min-w-0 flex-col gap-3 p-3">
      <section aria-label="Current processing status" className="flex min-w-0 flex-col gap-2">
        <div className="flex min-w-0 items-center justify-between gap-2">
          <SectionHeading>Run status</SectionHeading>
          <StatusIndicator status={displayStatus.key} label={displayStatus.label} />
        </div>
        {run.status === "processing" && run.currentStage ? (
          <div className="min-w-0 rounded-sm border border-border bg-background px-2.5 py-2">
            <p className="text-[10px] text-subtle">Current stage</p>
            <p className="mt-0.5 truncate text-[12px] font-medium text-foreground">
              {stageLabel(run.currentStage)}
            </p>
            {run.totalStages > 0 ? (
              <p className="mt-1 font-mono text-[10px] tabular-nums text-muted-foreground">
                {run.completedCount} of {run.totalStages} stages complete
              </p>
            ) : null}
          </div>
        ) : null}
      </section>

      {interrupted ? (
        <Alert>
          <AlertTitle>Previous run was interrupted</AlertTitle>
          <AlertDescription className="min-w-0 [overflow-wrap:anywhere]">
            The application closed while this project was processing. Start a new run when ready.
          </AlertDescription>
        </Alert>
      ) : null}

      <Separator />
      <section aria-label="Processing stages" className="min-w-0">
        <RunPanel
          run={run}
          cancelling={cancelling}
          cancelError={cancelError}
          showActions={false}
          showStatus={false}
          className="gap-2"
        />
      </section>

      <Separator />
      <div className="flex min-w-0 flex-col gap-2">
        {run.status === "processing" ? (
          <Button type="button" size="sm" variant="outline" disabled={cancelling} onClick={onCancel} className="w-full">
            {cancelling ? "Cancelling…" : "Cancel processing"}
          </Button>
        ) : (
          <Button type="button" size="sm" disabled={!canStart} onClick={onStart} className="w-full">
            <Play data-icon="inline-start" />
            {starting ? "Starting…" : run.status === "idle" ? "Process" : "Process again"}
          </Button>
        )}
        {run.status === "processing" && cancelling ? (
          <p role="status" className="text-center text-[10px] text-muted-foreground">Waiting for processing to stop…</p>
        ) : null}
      </div>
    </div>
  );
}

function ConfigurationContent({ runMode, setRunMode, visionConfigured, visionChecked, canStart }) {
  const aiSelected = isAiMode(runMode);
  return (
    <div className="flex min-w-0 flex-col gap-4 p-3">
      <section className="flex min-w-0 flex-col gap-2" aria-labelledby="processing-mode-title">
        <SectionHeading id="processing-mode-title">Processing mode</SectionHeading>
        <div className="flex min-w-0 flex-col gap-1.5" role="group" aria-label="Processing mode">
          <Button
            type="button"
            size="sm"
            variant={aiSelected ? "default" : "outline"}
            disabled={!canStart || !visionConfigured}
            onClick={() => setRunMode(RUN_MODES.AI)}
            aria-pressed={aiSelected}
            title={visionConfigured ? `Analyze up to ${VISION_FRAME_LIMIT} frames with AI` : "AI analysis requires provider configuration"}
            className="w-full justify-start"
          >
            <Sparkles data-icon="inline-start" />
            AI analysis
          </Button>
          <Button
            type="button"
            size="sm"
            variant={aiSelected ? "outline" : "default"}
            disabled={!canStart}
            onClick={() => setRunMode(RUN_MODES.OFFLINE)}
            aria-pressed={!aiSelected}
            className="w-full justify-start"
          >
            Offline extraction
          </Button>
        </div>
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {aiSelected
            ? `Analyzes up to ${VISION_FRAME_LIMIT} selected frames. Validated observations are reused.`
            : "Extracts frames without vision analysis or paid API calls."}
        </p>
      </section>

      <Separator />

      <section className="flex min-w-0 flex-col gap-2" aria-labelledby="provider-status-title">
        <h2 id="provider-status-title" className="text-[11px] font-medium text-muted-foreground">AI availability</h2>
        <div className="flex items-center justify-between gap-2">
          <span className="text-[12px] text-foreground">Provider credential</span>
          <Badge variant={visionConfigured ? "secondary" : "outline"}>
            {visionChecked ? (visionConfigured ? "Configured" : "Not configured") : "Checking"}
          </Badge>
        </div>
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {visionConfigured
            ? "AI analysis is available for this workspace."
            : visionChecked
              ? "AI analysis is disabled until the provider credential is configured and the application is restarted."
              : "Checking whether AI analysis is available…"}
        </p>
      </section>
    </div>
  );
}

function ProjectContent({ project }) {
  const sourceName = fileNameOf(project.sourceVideo.path);
  const extension = extensionOf(project.sourceVideo.path);
  const projectStatus = PROJECT_STATUS[project.processingStatus] ?? project.processingStatus;
  const rows = [
    ["Format", extension ? `.${extension}` : "Unavailable", true],
    ["File size", formatBytes(project.sourceVideo.sizeBytes), true],
    ["Processing status", projectStatus, false],
  ];

  return (
    <div className="flex min-w-0 flex-col gap-3 p-3">
      <section className="flex min-w-0 items-center gap-2" aria-label="Project name">
        <FolderOpen className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        <h2 className="min-w-0 truncate text-[13px] font-medium text-foreground" title={project.name}>{project.name}</h2>
      </section>
      <Separator />
      <dl className="flex min-w-0 flex-col gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <dt className="text-[10px] text-subtle">Source video</dt>
          <dd className="truncate text-[11px] text-foreground" title={sourceName}>{sourceName || "Unavailable"}</dd>
        </div>
        {rows.map(([label, value, monospace]) => (
          <div key={label} className="flex min-w-0 items-baseline justify-between gap-3">
            <dt className="shrink-0 text-[11px] text-muted-foreground">{label}</dt>
            <dd className={`min-w-0 truncate text-right text-[11px] text-foreground ${monospace ? "font-mono tabular-nums" : ""}`} title={value}>
              {value}
            </dd>
          </div>
        ))}
      </dl>
      <p className="text-[10px] leading-relaxed text-muted-foreground">Processing output stays inside this project.</p>
    </div>
  );
}

export default function ProcessingInspector({
  project,
  run,
  runMode,
  setRunMode,
  visionConfigured,
  visionChecked,
  canStart,
  starting,
  cancelling,
  cancelError,
  onStart,
  onCancel,
}) {
  return (
    <Tabs defaultValue="processing" className="flex h-full min-h-0 min-w-0 flex-col gap-0 overflow-hidden bg-surface">
      <TabsList variant="line" aria-label="Inspector sections" className="h-10 w-full shrink-0 gap-1 rounded-none border-b border-border px-2 py-0">
        <TabsTrigger value="processing" className="h-9 flex-1 rounded-sm px-1.5 text-[10px]">Processing</TabsTrigger>
        <TabsTrigger value="configuration" className="h-9 flex-1 rounded-sm px-1.5 text-[10px]">Configure</TabsTrigger>
        <TabsTrigger value="project" className="h-9 flex-1 rounded-sm px-1.5 text-[10px]">Project</TabsTrigger>
      </TabsList>

      <TabsContent value="processing" className="m-0 min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-hidden outline-none">
        <ProcessingContent
          project={project}
          run={run}
          canStart={canStart}
          starting={starting}
          cancelling={cancelling}
          cancelError={cancelError}
          onStart={onStart}
          onCancel={onCancel}
        />
      </TabsContent>
      <TabsContent value="configuration" className="m-0 min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-hidden outline-none">
        <ConfigurationContent
          runMode={runMode}
          setRunMode={setRunMode}
          visionConfigured={visionConfigured}
          visionChecked={visionChecked}
          canStart={canStart}
        />
      </TabsContent>
      <TabsContent value="project" className="m-0 min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-hidden outline-none">
        <ProjectContent project={project} />
      </TabsContent>
    </Tabs>
  );
}
