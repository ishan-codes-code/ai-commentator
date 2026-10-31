import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Mic, Play, Volume2 } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert.jsx";
import { Badge } from "@/components/ui/badge.jsx";
import { Button } from "@/components/ui/button.jsx";
import { Separator } from "@/components/ui/separator.jsx";
import { Spinner } from "@/components/ui/spinner.jsx";
import GlassCard from "@/primitives/GlassCard.jsx";
import StatusIndicator from "@/primitives/StatusIndicator.jsx";
import ArtifactBrowser from "@/components/ArtifactBrowser.jsx";
import RunPanel from "@/components/RunPanel.jsx";
import {
  applyWorkerMessage,
  createInitialRunState,
} from "@/lib/worker-view.js";
import {
  extensionOf,
  fileNameOf,
  fileUrlOf,
  formatBytes,
} from "@/lib/format.js";

const STATUS_LABELS = {
  idle: "Idle",
  processing: "Processing",
  done: "Done",
  error: "Error",
  cancelled: "Cancelled",
  interrupted: "Interrupted",
};

const STATUS_BADGE = {
  idle: "outline",
  processing: "default",
  done: "secondary",
  error: "destructive",
  cancelled: "outline",
  interrupted: "outline",
};

function AudioLane({ icon: Icon, title, children }) {
  return (
    <GlassCard className="flex flex-col gap-1.5 p-4">
      <div className="flex items-center gap-2">
        <Icon className="size-4 text-muted-foreground" aria-hidden />
        <h3 className="text-[13px] font-medium text-foreground">{title}</h3>
      </div>
      {children}
    </GlassCard>
  );
}

/**
 * Dedicated workspace for one opened project. The source video lives in
 * the project's isolated directory (loaded via audited IPC); processing
 * runs project-bound so artifacts can never leak across projects.
 * Audio lanes are honest empty states until extraction/TTS land.
 */
export default function WorkspaceSection({ projectId, onBack }) {
  const [project, setProject] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [previewFailed, setPreviewFailed] = useState(false);
  const [run, setRun] = useState(() => createInitialRunState());
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    let alive = true;
    async function load() {
      try {
        if (typeof window.api?.openProject !== "function") {
          throw new Error("Worker bridge unavailable (running outside Electron?).");
        }
        const res = await window.api.openProject(projectId);
        if (!res || res.ok !== true || !res.project) {
          throw new Error(res?.error ?? "Could not open the project.");
        }
        if (alive) {
          setProject(res.project);
          setRun((prev) =>
            res.project.processingStatus === "processing"
              ? { ...prev, status: "processing", settled: false }
              : prev,
          );
          setLoading(false);
        }
      } catch (err) {
        if (alive) {
          setError(err?.message ?? "Could not open the project.");
          setLoading(false);
        }
      }
    }
    load();

    let unsubscribe;
    if (typeof window.api?.onWorkerEvent === "function") {
      unsubscribe = window.api.onWorkerEvent((message) => {
        if (mountedRef.current) {
          setRun((prev) => applyWorkerMessage(prev, message));
        }
      });
    }
    return () => {
      alive = false;
      mountedRef.current = false;
      if (unsubscribe) {
        unsubscribe();
      }
    };
  }, [projectId]);

  useEffect(() => {
    if (
      run.status === "completed" ||
      run.status === "failed" ||
      run.status === "cancelled"
    ) {
      setCancelling(false);
      if (project && typeof window.api?.updateProject === "function") {
        const terminal =
          run.status === "completed"
            ? "done"
            : run.status === "failed"
              ? "error"
              : "cancelled";
        window.api
          .updateProject(project.id, { processingStatus: terminal })
          .catch(() => {});
      }
    }
  }, [run.status, project]);

  const processing = run.status === "processing";
  const canStart =
    project && !starting && !cancelling && run.settled && !processing;

  async function handleStart() {
    if (!canStart) {
      return;
    }
    setStarting(true);
    setCancelError(null);
    try {
      const res = await window.api.processVideo(project.sourceVideo.path, {
        projectId: project.id,
        skipVision: true,
      });
      if (!mountedRef.current) {
        return;
      }
      if (!res || res.ok !== true) {
        setRun({
          ...createInitialRunState(),
          status: "failed",
          settled: true,
          error: {
            stage: "validation",
            message: res?.error ?? "Could not start processing.",
          },
        });
      } else if (typeof window.api?.updateProject === "function") {
        window.api
          .updateProject(project.id, { processingStatus: "processing" })
          .catch(() => {});
      }
    } catch (err) {
      if (mountedRef.current) {
        setRun({
          ...createInitialRunState(),
          status: "failed",
          settled: true,
          error: {
            stage: "worker",
            message: err?.message ?? "Could not start processing.",
          },
        });
      }
    } finally {
      if (mountedRef.current) {
        setStarting(false);
      }
    }
  }

  async function handleCancel() {
    if (cancelling || run.status !== "processing") {
      return;
    }
    setCancelling(true);
    setCancelError(null);
    try {
      await window.api.cancelProcessing();
    } catch (err) {
      if (mountedRef.current) {
        setCancelling(false);
        setCancelError(err?.message ?? "Could not cancel processing.");
      }
    }
  }

  if (loading) {
    return (
      <div className="flex flex-col gap-4">
        <GlassCard className="flex items-center gap-2 p-5 text-[13px] text-muted-foreground">
          <Spinner />
          Opening project…
        </GlassCard>
      </div>
    );
  }

  if (error || !project) {
    return (
      <div className="flex flex-col gap-4">
        <Button
          type="button"
          variant="ghost"
          onClick={onBack}
          className="self-start"
        >
          <ArrowLeft data-icon="inline-start" />
          Projects
        </Button>
        <Alert variant="destructive">
          <AlertTitle>Could not open the project</AlertTitle>
          <AlertDescription>{error ?? "Project not found."}</AlertDescription>
        </Alert>
      </div>
    );
  }

  const fileUrl = fileUrlOf(project.sourceVideo.path);
  // `file://` media loads only from a `file:` origin (packaged app).
  // Under the dev server the attempt is rejected, so go straight to
  // the honest fallback instead of logging a console error.
  const canPreviewFiles =
    typeof window !== "undefined" && window.location.protocol === "file:";
  const indicatorStatus =
    run.status === "completed"
      ? "done"
      : run.status === "failed"
        ? "error"
        : run.status;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="ghost"
          onClick={onBack}
          aria-label="Back to projects"
        >
          <ArrowLeft data-icon="inline-start" />
          Projects
        </Button>
        <Separator orientation="vertical" className="h-4" />
        <h2 className="min-w-0 flex-1 truncate text-[15px] font-semibold tracking-[-0.01em] text-foreground">
          {project.name}
        </h2>
        <Badge variant={STATUS_BADGE[run.status] ?? "outline"}>
          {STATUS_LABELS[run.status] ?? run.status}
        </Badge>
      </div>

      <GlassCard className="flex flex-col gap-3 p-5">
        {fileUrl && !previewFailed && canPreviewFiles ? (
          <video
            key={project.sourceVideo.path}
            src={fileUrl}
            controls
            preload="metadata"
            aria-label={`${project.name} gameplay preview`}
            onError={() => setPreviewFailed(true)}
            className="aspect-video w-full rounded-card border border-border bg-background"
          >
            <track kind="captions" />
            Preview unavailable for this file.
          </video>
        ) : (
          <div className="flex flex-col items-center gap-2 rounded-card border border-border bg-background px-4 py-8 text-center">
            <p className="text-[13px] font-medium text-foreground">
              Preview unavailable for this file
            </p>
            <p className="max-w-sm text-xs text-muted-foreground">
              The desktop shell could not decode this video for preview.
              Processing uses the Python engine and is unaffected.
            </p>
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground">
            {fileNameOf(project.sourceVideo.path)} ·{" "}
            {formatBytes(project.sourceVideo.sizeBytes)} · .
            {extensionOf(project.sourceVideo.path)}
          </span>
          <StatusIndicator
            status={indicatorStatus}
            label={STATUS_LABELS[run.status] ?? run.status}
          />
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="default"
            disabled={!canStart}
            onClick={handleStart}
          >
            {starting ? (
              <>
                <Spinner data-icon="inline-start" />
                Starting…
              </>
            ) : (
              <>
                <Play data-icon="inline-start" />
                Process
              </>
            )}
          </Button>
          {processing ? (
            <Button
              type="button"
              variant="outline"
              disabled={cancelling}
              onClick={handleCancel}
            >
              {cancelling ? (
                <>
                  <Spinner data-icon="inline-start" />
                  Cancelling…
                </>
              ) : (
                "Cancel processing"
              )}
            </Button>
          ) : null}
        </div>
        {project.processingStatus === "interrupted" &&
        run.status === "idle" ? (
          <Alert variant="default">
            <AlertTitle>Previous run was interrupted</AlertTitle>
            <AlertDescription>
              The application closed while this project was processing.
              Nothing was marked complete — start a new run when ready.
            </AlertDescription>
          </Alert>
        ) : null}
        <p className="text-xs text-muted-foreground">
          Offline-safe: vision analysis is skipped, so no API key is needed
          and no paid calls are made. Output stays inside this project.
        </p>
      </GlassCard>

      <div className="grid gap-4 md:grid-cols-2">
        <AudioLane icon={Volume2} title="Original game audio">
          <p className="text-xs text-muted-foreground">
            Audio extraction is not implemented yet — this lane will carry
            the gameplay soundtrack.
          </p>
        </AudioLane>
        <AudioLane icon={Mic} title="AI commentary">
          <p className="text-xs text-muted-foreground">
            Text-to-speech is not implemented yet — generated commentary
            will play here.
          </p>
        </AudioLane>
      </div>

      {run.status !== "idle" ? (
        <RunPanel
          run={run}
          cancelling={cancelling}
          cancelError={cancelError}
          onCancel={handleCancel}
          onRetry={handleStart}
          onChooseDifferent={onBack}
          chooseDifferentLabel="Back to projects"
        />
      ) : null}

      <ArtifactBrowser projectId={project.id} refreshSignal={run.status} />
    </div>
  );
}
