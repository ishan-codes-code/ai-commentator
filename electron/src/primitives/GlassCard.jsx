import { cn } from "cn";

/**
 * Solid content card for canvas/surface areas.
 * Flat fill + hairline border; glass is reserved for layered surfaces.
 */
export default function GlassCard({ className, children, ...props }) {
  return (
    <div
      className={cn(
        "rounded-card border border-border bg-card shadow-none",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}
