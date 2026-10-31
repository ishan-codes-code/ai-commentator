import { useRef, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button.jsx";
import { Separator } from "@/components/ui/separator.jsx";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs.jsx";
import ArtifactBrowser from "@/components/ArtifactBrowser.jsx";
import VideoPreview from "./VideoPreview.jsx";

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
