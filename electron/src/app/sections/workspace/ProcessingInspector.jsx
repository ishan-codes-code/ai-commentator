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

const STATUS_LABELS = {
  idle: "Idle",
  processing: "Processing",
  completed: "Done",
  failed: "Error",
  cancelled: "Cancelled",
  interrupted: "Interrupted",
};

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
  const status = run.status === "completed" ? "done" : run.status === "failed" ? "error" : run.status;

  return (
    <Tabs defaultValue="processing" className="flex h-full min-h-0 flex-col gap-0 bg-surface">
      <TabsList variant="line" aria-label="Inspector sections" className="h-9 w-full shrink-0 gap-1 rounded-none border-b border-border px-2 py-0">
        <TabsTrigger value="processing" className="h-8 flex-1 rounded-sm px-2 text-[11px]">Processing</TabsTrigger>
        <TabsTrigger value="project" className="h-8 flex-1 rounded-sm px-2 text-[11px]">Project</TabsTrigger>
      </TabsList>

      <TabsContent value="processing" className="m-0 min-h-0 flex-1 overflow-y-auto p-3">
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[12px] font-medium text-foreground">Run status</span>
            <StatusIndicator status={status} label={STATUS_LABELS[run.status] ?? run.status} />
          </div>

          <section aria-labelledby="processing-mode-title" className="flex flex-col gap-2">
            <h2 id="processing-mode-title" className="text-[11px] font-medium text-muted-foreground">Processing mode</h2>
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Processing mode">
              <Button
                type="button"
                size="sm"
                variant={isAiMode(runMode) ? "default" : "outline"}
                disabled={!canStart || !visionConfigured}
                onClick={() => setRunMode(RUN_MODES.AI)}
                aria-pressed={isAiMode(runMode)}
                title={visionConfigured ? `Analyze up to ${VISION_FRAME_LIMIT} frames with AI` : "AI analysis needs a Baseten API key"}
              >
                <Sparkles data-icon="inline-start" />
                AI analysis
              </Button>
              <Button
                type="button"
                size="sm"
                variant={isAiMode(runMode) ? "outline" : "default"}
                disabled={!canStart}
                onClick={() => setRunMode(RUN_MODES.OFFLINE)}
                aria-pressed={!isAiMode(runMode)}
              >
                Offline
              </Button>
            </div>
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              {isAiMode(runMode)
                ? `Analyzes up to ${VISION_FRAME_LIMIT} selected frames with the Baseten vision model. Validated observations are reused.`
                : visionChecked && !visionConfigured
                  ? "Offline extraction skips vision analysis and requires no API key. Set BASETEN_API_KEY and restart to enable AI analysis."
                  : "Offline extraction skips vision analysis and makes no paid API calls."}
            </p>
          </section>

          {project.processingStatus === "interrupted" && run.status === "idle" ? (
            <Alert>
              <AlertTitle>Previous run was interrupted</AlertTitle>
              <AlertDescription>
                The application closed while this project was processing. Start a new run when ready.
              </AlertDescription>
            </Alert>
          ) : null}

          <div className="flex flex-wrap gap-2">
            {run.status === "processing" ? (
              <Button type="button" size="sm" variant="outline" disabled={cancelling} onClick={onCancel}>
                {cancelling ? "Cancelling…" : "Cancel processing"}
              </Button>
            ) : (
              <Button type="button" size="sm" disabled={!canStart} onClick={onStart}>
                <Play data-icon="inline-start" />
                {starting ? "Starting…" : run.status === "idle" ? "Process" : "Process again"}
              </Button>
            )}
          </div>

          <Separator />
          <RunPanel
            run={run}
            cancelling={cancelling}
            cancelError={cancelError}
            showActions={false}
            className="gap-3 p-3"
          />
        </div>
      </TabsContent>

      <TabsContent value="project" className="m-0 min-h-0 flex-1 overflow-y-auto p-3">
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <FolderOpen className="size-4 text-muted-foreground" aria-hidden />
            <h2 className="min-w-0 truncate text-[12px] font-medium text-foreground">{project.name}</h2>
          </div>
          <Separator />
          <dl className="flex flex-col gap-2">
            <div className="flex flex-col gap-0.5">
              <dt className="text-[10px] text-subtle">Source video</dt>
              <dd className="truncate text-[11px] text-foreground">{fileNameOf(project.sourceVideo.path)}</dd>
            </div>
            <div className="flex items-baseline justify-between gap-2">
              <dt className="text-[11px] text-muted-foreground">Format</dt>
              <dd className="font-mono text-[11px] text-foreground">.{extensionOf(project.sourceVideo.path)}</dd>
            </div>
            <div className="flex items-baseline justify-between gap-2">
              <dt className="text-[11px] text-muted-foreground">File size</dt>
              <dd className="font-mono text-[11px] text-foreground">{formatBytes(project.sourceVideo.sizeBytes)}</dd>
            </div>
            <div className="flex items-baseline justify-between gap-2">
              <dt className="text-[11px] text-muted-foreground">Project status</dt>
              <dd><Badge variant="outline">{project.processingStatus}</Badge></dd>
            </div>
          </dl>
          <p className="text-[11px] text-muted-foreground">Processing output stays inside this project.</p>
        </div>
      </TabsContent>
    </Tabs>
  );
}
