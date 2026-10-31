import { motion } from "motion/react";
import {
  AudioLines,
  LayoutDashboard,
  MessagesSquare,
  Settings,
  Trophy,
} from "lucide-react";
import { cn } from "cn";
import { Separator } from "@/components/ui/separator.jsx";
import { MOTION } from "@/lib/tokens.js";

const ITEMS = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "matches", label: "Matches", icon: Trophy },
  { id: "commentary", label: "Commentary", icon: MessagesSquare },
  { id: "settings", label: "Settings", icon: Settings },
];

/**
 * Application sidebar: brand mark, section navigation, environment
 * footer. Solid surface with a right hairline; density suited to
 * desktop software (13px labels, 8px control rhythm).
 */
export default function Sidebar({ active, onSelect }) {
  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-surface">
      <div className="flex h-12 items-center gap-2.5 px-4">
        <span className="flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <AudioLines className="size-4" aria-hidden />
        </span>
        <span className="flex min-w-0 flex-col leading-tight">
          <span className="truncate text-[13px] font-semibold tracking-[-0.01em] text-foreground">
            AI Commentator
          </span>
          <span className="text-[11px] text-subtle">Obsidian Studio</span>
        </span>
      </div>
      <Separator />
      <nav
        aria-label="Sections"
        className="flex flex-1 flex-col gap-0.5 overflow-y-auto px-2.5 py-3"
      >
        {ITEMS.map((item) => {
          const Icon = item.icon;
          const selected = item.id === active;
          return (
            <button
              key={item.id}
              type="button"
              aria-current={selected ? "page" : undefined}
              onClick={() => onSelect(item.id)}
              className={cn(
                "relative flex items-center gap-2.5 rounded-md px-2.5 py-[7px] text-[13px]",
                "text-muted-foreground transition-colors duration-150",
                "hover:bg-accent hover:text-accent-foreground",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-0",
                "disabled:cursor-not-allowed disabled:opacity-50",
                selected && "bg-accent font-medium text-foreground",
              )}
            >
              {selected ? (
                <motion.span
                  layoutId="sidebar-active"
                  transition={{ duration: MOTION.standard, ease: "easeOut" }}
                  className="absolute top-1/2 left-0 h-4 w-[3px] -translate-y-1/2 rounded-full bg-primary"
                />
              ) : null}
              <Icon className="size-4 shrink-0" aria-hidden />
              {item.label}
            </button>
          );
        })}
      </nav>
      <Separator />
      <div className="flex items-center justify-between px-4 py-2.5">
        <span className="text-[11px] text-subtle">Offline engine</span>
        <span className="font-mono text-[11px] text-subtle">v1.0.0</span>
      </div>
    </aside>
  );
}
