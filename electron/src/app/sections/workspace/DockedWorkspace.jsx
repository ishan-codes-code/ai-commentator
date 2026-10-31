import { useState } from "react";
import { AudioLines } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs.jsx";

const TRACKS = [
  { id: "video", label: "Gameplay video", detail: "Source available" },
  { id: "game-audio", label: "Original game audio", detail: "Not extracted" },
  { id: "commentary", label: "AI commentary", detail: "Not generated" },
];

function AudioWorkspace() {
  return (
    <div className="grid h-full min-h-0 grid-rows-3 px-3">
      {TRACKS.map((track) => (
        <div key={track.id} className="grid min-h-0 grid-cols-[148px_minmax(0,1fr)] items-center gap-3 border-b border-border last:border-b-0">
          <div className="flex min-w-0 flex-col gap-0.5 pr-2">
            <span className="truncate text-[11px] font-medium text-foreground">{track.label}</span>
            <span className="truncate text-[10px] text-subtle">{track.detail}</span>
          </div>
          <div className="flex h-7 min-w-0 items-center rounded-sm border border-border bg-background px-3">
            <span className="truncate text-[10px] text-subtle">Playback lane unavailable</span>
          </div>
        </div>
      ))}
    </div>
  );
}

const PANELS = [{ id: "audio", label: "Audio", icon: AudioLines, component: AudioWorkspace }];

export default function DockedWorkspace() {
  const [activePanel, setActivePanel] = useState("audio");
  const ActivePanel = PANELS.find((panel) => panel.id === activePanel)?.component ?? AudioWorkspace;

  return (
    <section aria-label="Bottom workspace" className="h-[196px] min-h-0 border-t border-border bg-surface">
      <Tabs value={activePanel} onValueChange={setActivePanel} className="flex h-full min-h-0 flex-col gap-0">
        <TabsList variant="line" aria-label="Bottom workspace panels" className="h-9 w-full shrink-0 gap-1 rounded-none border-b border-border px-3 py-0">
          {PANELS.map(({ id, label, icon: Icon }) => (
            <TabsTrigger key={id} value={id} className="h-8 flex-initial rounded-sm px-3 text-[11px]">
              <Icon className="size-3.5" aria-hidden />
              {label}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value={activePanel} className="m-0 min-h-0 overflow-hidden">
          <ActivePanel />
        </TabsContent>
      </Tabs>
    </section>
  );
}
