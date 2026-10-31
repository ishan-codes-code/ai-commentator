import {
  AudioLines,
  AudioWaveform,
  Clapperboard,
  ScanSearch,
  Settings,
  Sparkles,
} from "lucide-react";
import { cn } from "cn";
import { Separator } from "@/components/ui/separator.jsx";

const ITEMS = [
  { id: "dashboard", label: "Editor", icon: Clapperboard },
  { id: "prompts", label: "AI Prompts", icon: Sparkles },
  { id: "matches", label: "AI Analysis", icon: ScanSearch },
  { id: "audio", label: "Audio", icon: AudioWaveform },
  { id: "settings", label: "Settings", icon: Settings },
];

export default function Sidebar({ active, onSelect }) {
  return (
    <aside className="flex w-[196px] shrink-0 flex-col border-r border-border bg-surface">
      <div className="flex h-11 items-center gap-2.5 px-3.5">
        <span className="flex size-6 items-center justify-center rounded-sm bg-foreground text-background">
          <AudioLines className="size-3.5" aria-hidden />
        </span>
        <span className="truncate text-[12px] font-semibold tracking-[-0.01em] text-foreground">
          Commentary Studio
        </span>
      </div>
      <Separator />
      <nav aria-label="Studio sections" className="flex flex-1 flex-col gap-0.5 px-2 py-3">
        <p className="px-2.5 pb-2 text-[10px] font-medium uppercase tracking-[0.12em] text-subtle">
          Workspace
        </p>
        {ITEMS.map(({ id, label, icon: Icon }) => {
          const selected = id === active;
          return (
            <button
              key={id}
              type="button"
              aria-current={selected ? "page" : undefined}
              onClick={() => onSelect(id)}
              className={cn(
                "flex items-center gap-2.5 rounded-sm px-2.5 py-[7px] text-left text-[12px] transition-colors duration-150",
                "text-muted-foreground hover:bg-accent hover:text-foreground",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                selected && "bg-accent font-medium text-foreground",
              )}
            >
              <Icon className="size-4 shrink-0" aria-hidden />
              {label}
            </button>
          );
        })}
      </nav>
      <Separator />
      <div className="flex items-center justify-between px-3.5 py-2.5">
        <span className="text-[10px] text-subtle">Local workspace</span>
        <span className="font-mono text-[10px] text-subtle">1.0.0</span>
      </div>
    </aside>
  );
}
