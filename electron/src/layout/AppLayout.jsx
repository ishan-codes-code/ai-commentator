import { cn } from "cn";

/** Fixed viewport frame. Scrolling belongs to individual sections/panels. */
export default function AppLayout({ sidebar, header, children, className }) {
  return (
    <div className="flex h-full min-h-0 min-w-0 overflow-hidden bg-background text-foreground">
      {sidebar}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        {header}
        <main className={cn("min-h-0 min-w-0 flex-1 overflow-hidden", className)}>
          {children}
        </main>
      </div>
    </div>
  );
}
