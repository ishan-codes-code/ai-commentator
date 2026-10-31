import {
  AudioLines,
  AudioWaveform,
  Clapperboard,
  MessagesSquare,
  ScanSearch,
  Settings,
  Sparkles,
} from "lucide-react";
import { cn } from "cn";
import { Separator } from "@/components/ui/separator.jsx";

const NAV_ITEMS = [
  { id: "dashboard", label: "Dashboard", icon: Clapperboard },
  { id: "matches", label: "AI Analysis", icon: ScanSearch },
  { id: "commentary", label: "Commentary", icon: MessagesSquare },
  { id: "prompts", label: "AI Prompts", icon: Sparkles },
  { id: "audio", label: "Audio", icon: AudioWaveform },
  { id: "settings", label: "Settings", icon: Settings },
];

export default function Sidebar({ active, onSelect, collapsed, inWorkspace }) {
  const items = inWorkspace
    ? [{ id: "editor", label: "Editor", icon: Clapperboard }, ...NAV_ITEMS]
    : NAV_ITEMS;

  return (
    <aside
      className={cn(
        "flex h-full shrink-0 flex-col overflow-hidden border-r border-border bg-surface transition-[width] duration-150 motion-reduce:transition-none",
        collapsed ? "w-[52px]" : "w-[196px]",
      )}
      aria-label="Application navigation"
    >
      <div className={cn("flex h-11 shrink-0 items-center gap-2.5", collapsed ? "justify-center px-1" : "px-3.5")}>
        <span className="flex size-6 shrink-0 items-center justify-center rounded-sm bg-foreground text-background">
          <AudioLines className="size-3.5" aria-hidden />
        </span>
        {!collapsed ? (
          <span className="truncate text-[12px] font-semibold tracking-[-0.01em] text-foreground">
            Commentary Studio
          </span>
        ) : null}
      </div>
      <Separator />
      <nav
        id="studio-navigation"
        aria-label="Studio sections"
        className={cn("flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto py-3", collapsed ? "items-center px-1" : "px-2")}
      >
        {!collapsed ? (
          <p className="px-2.5 pb-2 text-[10px] font-medium uppercase tracking-[0.12em] text-subtle">
            Workspace
          </p>
        ) : null}
        {items.map(({ id, label, icon: Icon }) => {
          const selected = id === active;
          return (
            <button
              key={id}
              type="button"
              aria-label={label}
              aria-current={selected ? "page" : undefined}
              title={collapsed ? label : undefined}
              onClick={() => onSelect(id)}
              className={cn(
                "flex shrink-0 items-center gap-2.5 rounded-sm text-[12px] transition-colors duration-150",
                collapsed ? "size-9 justify-center px-0" : "w-full px-2.5 py-[7px] text-left",
                "text-muted-foreground hover:bg-accent hover:text-foreground",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                selected && "bg-accent font-medium text-foreground",
              )}
            >
              <Icon className="size-4 shrink-0" aria-hidden />
              {!collapsed ? <span className="truncate">{label}</span> : null}
            </button>
          );
        })}
      </nav>
      <Separator />
      {!collapsed ? (
        <div className="flex shrink-0 items-center justify-between px-3.5 py-2.5">
          <span className="text-[10px] text-subtle">Local workspace</span>
          <span className="font-mono text-[10px] text-subtle">1.0.0</span>
        </div>
      ) : (
        <div className="h-9 shrink-0" aria-hidden="true" />
      )}
    </aside>
  );
}
