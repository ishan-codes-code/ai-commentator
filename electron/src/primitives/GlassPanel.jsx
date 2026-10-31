import { cn } from "cn";

/**
 * Main glass surface: app panel, dialogs, floating layers.
 * Static blur — never animate the blur itself; animate a wrapper instead.
 */
export default function GlassPanel({ className, children, ...props }) {
  return (
    <div
      className={cn(
        "rounded-panel border-glass-border bg-glass shadow-panel backdrop-blur-glass border",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}
