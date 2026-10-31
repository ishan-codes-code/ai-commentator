import { cn } from "cn";
import GlassPanel from "@/primitives/GlassPanel.jsx";

/**
 * Desktop frame: sidebar + glass main panel with a single static
 * ambient glow. Content scrolls inside the panel; the window never scrolls.
 */
export default function AppLayout({ sidebar, children, className }) {
  return (
    <div className="flex h-full gap-4 bg-background p-4 text-foreground">
      {sidebar}
      <main className="relative min-w-0 flex-1">
        <div
          aria-hidden
          className="pointer-events-none absolute -top-24 left-1/4 h-64 w-96 rounded-full bg-primary/10 blur-3xl"
        />
        <GlassPanel className={cn("relative h-full overflow-hidden p-5", className)}>
          <div className="h-full overflow-y-auto">{children}</div>
        </GlassPanel>
      </main>
    </div>
  );
}
