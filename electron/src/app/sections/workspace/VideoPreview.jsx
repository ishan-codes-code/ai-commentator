import { useRef, useState } from "react";
import { AlertCircle, Film, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button.jsx";
import { extensionOf, fileNameOf, formatBytes, projectMediaUrl } from "@/lib/format.js";

function formatTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "--:--";
  const whole = Math.floor(seconds);
  const secondsPart = String(whole % 60).padStart(2, "0");
  const minutes = Math.floor(whole / 60);
  if (minutes >= 60) {
    return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}:${secondsPart}`;
  }
  return `${String(minutes).padStart(2, "0")}:${secondsPart}`;
}

function EmptyPreview() {
  return (
    <div className="flex h-full min-h-0 flex-col items-center justify-center gap-2 text-center" role="status">
      <Film className="size-5 text-subtle" aria-hidden />
      <p className="text-[13px] font-medium text-foreground">No source video available</p>
      <p className="text-[11px] text-muted-foreground">This project does not have a source video to preview.</p>
    </div>
  );
}

export default function VideoPreview({ project, videoRef }) {
  const internalVideoRef = useRef(null);
  const [status, setStatus] = useState("loading");
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(null);
  const [dimensions, setDimensions] = useState(null);
  const [playing, setPlaying] = useState(false);
  const sourcePath = project?.sourceVideo?.path;
  const fileName = fileNameOf(sourcePath);
  const hasSource = Boolean(project?.id && sourcePath && fileName);
  const url = hasSource ? projectMediaUrl(project.id, "source", fileName) : "";
  const setVideoRef = (node) => {
    internalVideoRef.current = node;
    if (typeof videoRef === "function") videoRef(node);
    else if (videoRef) videoRef.current = node;
  };

  function handleMetadata(event) {
    const video = event.currentTarget;
    setDuration(Number.isFinite(video.duration) ? video.duration : null);
    setDimensions(video.videoWidth > 0 && video.videoHeight > 0
      ? `${video.videoWidth} × ${video.videoHeight}`
      : null);
    setStatus("ready");
  }

  function retry() {
    const video = internalVideoRef.current;
    if (!video) return;
    setStatus("loading");
    video.load();
  }

  return (
    <section aria-label="Video preview" className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-background">
      <header className="flex h-8 shrink-0 min-w-0 items-center justify-between gap-3 border-b border-border bg-surface px-3">
        <span className="min-w-0 truncate text-[11px] font-medium text-foreground" title={hasSource ? fileName : undefined}>
          {hasSource ? fileName : "Source video"}
        </span>
        <div className="flex shrink-0 items-center gap-3 text-[10px] text-subtle">
          {dimensions ? <span className="font-mono tabular-nums">{dimensions}</span> : null}
          <span aria-live="polite">{status === "loading" ? "Loading" : status === "error" ? "Unavailable" : playing ? "Playing" : "Paused"}</span>
        </div>
      </header>

      <div className="relative flex min-h-0 min-w-0 flex-1 items-center justify-center overflow-hidden bg-background">
        {!hasSource ? <EmptyPreview /> : (
          <>
            <video
              ref={setVideoRef}
              key={sourcePath}
              src={url}
              controls
              preload="metadata"
              aria-label={`${project.name} source video preview`}
              onLoadStart={() => setStatus("loading")}
              onLoadedData={() => setStatus("ready")}
              onLoadedMetadata={handleMetadata}
              onDurationChange={(event) => setDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : null)}
              onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onEnded={() => setPlaying(false)}
              onError={() => setStatus("error")}
              className="block max-h-full max-w-full object-contain"
              style={{ width: "100%", height: "100%" }}
            >
              Your browser cannot play this video.
            </video>
            {status === "loading" ? (
              <div role="status" className="pointer-events-none absolute inset-0 flex items-center justify-center bg-background/70 text-[11px] text-muted-foreground">
                Loading video metadata…
              </div>
            ) : null}
            {status === "error" ? (
              <div role="alert" className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-background/95 px-4 text-center">
                <AlertCircle className="size-4 text-destructive" aria-hidden />
                <p className="text-[13px] font-medium text-foreground">Video could not be played</p>
                <p className="max-w-sm text-[11px] text-muted-foreground">
                  The source may be unavailable or use an unsupported format. Playback error code: {internalVideoRef.current?.error?.code ?? "unknown"}.
                </p>
                <Button type="button" size="sm" variant="outline" onClick={retry}>
                  <RotateCcw data-icon="inline-start" />
                  Retry
                </Button>
              </div>
            ) : null}
          </>
        )}
      </div>

      <footer className="flex h-8 shrink-0 min-w-0 items-center justify-between gap-3 border-t border-border bg-surface px-3">
        <span className="min-w-0 truncate font-mono text-[10px] text-subtle">
          {hasSource ? `${extensionOf(sourcePath).toUpperCase()} · ${formatBytes(project.sourceVideo.sizeBytes)}` : "No media loaded"}
        </span>
        {hasSource ? (
          <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground" aria-label="Playback time">
            {formatTime(currentTime)} / {formatTime(duration)}
          </span>
        ) : null}
      </footer>
    </section>
  );
}
