import { cn } from "cn";
import AppHeader from "./AppHeader.jsx";

/**
 * Desktop frame: sidebar + main column (slim header bar + content).
 * Flat charcoal surfaces with hairline separation — no glow, no blur,
 * no floating-panel stacking. Content scrolls; the window never scrolls.
 */
export default function AppLayout({ sidebar, header, children, className }) {
  return (
    <div className="flex h-full bg-background text-foreground">
      {sidebar}
      <div className="flex min-w-0 flex-1 flex-col">
        {header}
        <main className={cn("min-h-0 flex-1 overflow-y-auto", className)}>
          <div className="mx-auto w-full max-w-5xl px-6 py-6">{children}</div>
        </main>
      </div>
    </div>
  );
}
