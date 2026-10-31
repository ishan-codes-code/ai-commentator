import { cn } from "cn";

/**
 * Desktop frame: sidebar + main column (slim header bar + content).
 * Flat charcoal surfaces with hairline separation — no glow, no blur,
 * no floating-panel stacking. Content scrolls; the window never scrolls.
 */
export default function AppLayout({ sidebar, header, children, audioTray = false, className }) {
  return (
    <div className="flex h-full min-h-0 bg-background text-foreground">
      {sidebar}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {header}
        <main className={cn("min-h-0 min-w-0 flex-1 overflow-y-auto", className)}>
          <div className="h-full min-h-0 w-full px-5 py-4">{children}</div>
        </main>
        {audioTray ? (
          <div className="shrink-0 border-t border-border bg-surface">
            <div className="flex h-8 items-center justify-between border-b border-border px-4">
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-medium text-foreground">Audio tray</span>
                <span className="text-[10px] text-subtle">Playback lanes</span>
              </div>
              <span className="font-mono text-[10px] text-subtle">No audio generated · Timeline visualization unavailable</span>
            </div>
            <div className="grid h-[88px] grid-rows-3 divide-y divide-border px-4">
              {["Gameplay video", "Original game audio", "AI commentary"].map((track) => (
                <div key={track} className="flex min-h-0 items-center gap-3">
                  <span className="w-36 shrink-0 truncate text-[10px] text-muted-foreground">{track}</span>
                  <div className="h-5 min-w-0 flex-1 rounded-sm border border-border bg-background/60" aria-label={`${track} track is empty`} />
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
