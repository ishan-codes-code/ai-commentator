import { useRef, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button.jsx";
import { Separator } from "@/components/ui/separator.jsx";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs.jsx";
import ArtifactBrowser from "@/components/ArtifactBrowser.jsx";
import { extensionOf, fileNameOf, formatBytes, projectMediaUrl } from "@/lib/format.js";

function formatTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "--:--";
  const whole = Math.floor(seconds);
  const minutes = Math.floor(whole / 60);
  const remainder = String(whole % 60).padStart(2, "0");
  return `${minutes}:${remainder}`;
}

function VideoPreview({ project, videoRef }) {
  const [previewFailed, setPreviewFailed] = useState(false);
  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const videoUrl = projectMediaUrl(project.id, "source", fileNameOf(project.sourceVideo.path));

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col bg-background">
      <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden px-3 py-2">
        {previewFailed ? (
          <div role="status" className="flex aspect-video max-h-full w-full flex-col items-center justify-center gap-2 rounded-sm border border-border bg-surface px-4 text-center">
            <p className="text-[13px] font-medium text-foreground">Preview unavailable</p>
            <p className="max-w-sm text-[11px] text-muted-foreground">
              This video could not be opened by the desktop media player. Processing is unaffected.
            </p>
          </div>
        ) : (
          <video
            ref={videoRef}
            key={project.sourceVideo.path}
            src={videoUrl}
            controls
            preload="metadata"
            aria-label={`${project.name} gameplay preview`}
            onError={() => setPreviewFailed(true)}
            onLoadedMetadata={(event) => setDuration(event.currentTarget.duration)}
            onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
            className="h-full w-full rounded-sm border border-border bg-background object-contain"
          >
            Preview unavailable for this file.
          </video>
        )}
      </div>
      <div className="flex h-8 shrink-0 items-center justify-between gap-3 border-t border-border px-3">
        <span className="min-w-0 truncate font-mono text-[10px] text-muted-foreground">
          {fileNameOf(project.sourceVideo.path)} · {formatBytes(project.sourceVideo.sizeBytes)} · .{extensionOf(project.sourceVideo.path)}
        </span>
        <span className="shrink-0 font-mono text-[10px] tabular-nums text-subtle" aria-label="Playback time">
          {formatTime(currentTime)} / {formatTime(duration)}
        </span>
      </div>
    </div>
  );
}

export default function EditorCenter({ project, refreshSignal, onBack }) {
  const [tab, setTab] = useState("preview");
  const videoRef = useRef(null);

  function handleTabChange(nextTab) {
    if (nextTab !== "preview") {
      videoRef.current?.pause();
    }
    setTab(nextTab);
  }

  return (
    <Tabs value={tab} onValueChange={handleTabChange} className="flex h-full min-h-0 min-w-0 flex-col gap-0 bg-background">
      <div className="flex h-10 shrink-0 min-w-0 items-center gap-2 border-b border-border bg-surface px-2">
        <Button type="button" size="sm" variant="ghost" onClick={onBack}>
          <ArrowLeft data-icon="inline-start" />
          Projects
        </Button>
        <Separator orientation="vertical" className="h-4" />
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-foreground">{project.name}</span>
        <TabsList variant="line" aria-label="Project workspace views" className="h-9 shrink-0 gap-1 rounded-none p-0">
          <TabsTrigger value="preview" className="h-8 flex-initial rounded-sm px-2.5 text-[11px]">Preview</TabsTrigger>
          <TabsTrigger value="artifacts" className="h-8 flex-initial rounded-sm px-2.5 text-[11px]">Artifacts</TabsTrigger>
        </TabsList>
      </div>

      <TabsContent value="preview" forceMount className="m-0 min-h-0 min-w-0 flex-1 overflow-hidden data-[state=inactive]:hidden">
        <VideoPreview project={project} videoRef={videoRef} />
      </TabsContent>
      <TabsContent value="artifacts" forceMount className="m-0 min-h-0 min-w-0 flex-1 overflow-y-auto p-3 data-[state=inactive]:hidden">
        <ArtifactBrowser projectId={project.id} refreshSignal={refreshSignal} className="min-h-full" />
      </TabsContent>
    </Tabs>
  );
}
