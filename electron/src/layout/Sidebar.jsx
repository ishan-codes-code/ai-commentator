import { motion } from "motion/react";
import {
  AudioLines,
  LayoutDashboard,
  MessagesSquare,
  Settings,
  Trophy,
} from "lucide-react";
import { cn } from "cn";
import { MOTION } from "@/lib/tokens.js";

const ITEMS = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "matches", label: "Matches", icon: Trophy },
  { id: "commentary", label: "Commentary", icon: MessagesSquare },
  { id: "settings", label: "Settings", icon: Settings },
];

/**
 * App sidebar: brand mark + section navigation.
 * Active section is a real button state with a shared layout indicator.
 */
export default function Sidebar({ active, onSelect }) {
  return (
    <aside className="flex w-60 shrink-0 flex-col gap-6">
      <div className="flex items-center gap-3 px-2">
        <span className="flex size-9 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <AudioLines className="size-5" data-icon="nav-brand" />
        </span>
        <span className="flex flex-col">
          <span className="text-sm font-semibold tracking-tight text-foreground">
            AI Commentator
          </span>
          <span className="text-xs text-subtle">Obsidian AI</span>
        </span>
      </div>
      <nav aria-label="Sections" className="flex flex-col gap-1">
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
                "relative flex items-center gap-3 rounded-md px-3 py-2 text-sm",
                "text-muted-foreground transition-colors",
                "hover:bg-accent hover:text-accent-foreground",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                selected && "bg-accent font-medium text-foreground",
              )}
            >
              {selected ? (
                <motion.span
                  layoutId="sidebar-active"
                  transition={{ duration: MOTION.standard, ease: "easeOut" }}
                  className="absolute top-1/2 left-0 h-5 w-1 -translate-y-1/2 rounded-full bg-primary"
                />
              ) : null}
              <Icon className="size-4 shrink-0" aria-hidden />
              {item.label}
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
