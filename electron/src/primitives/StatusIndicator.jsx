import { motion } from "motion/react";
import { cn } from "cn";
import { MOTION } from "@/lib/tokens.js";

const STYLES = {
  idle: "bg-muted-foreground",
  processing: "bg-secondary",
  done: "bg-success",
  error: "bg-destructive",
  cancelled: "bg-warning",
};

/**
 * Status dot + label. Color never stands alone: the label names the state.
 * The processing dot pulses via opacity only (cheap, stops on settle).
 */
export default function StatusIndicator({ status = "idle", label, className }) {
  const normalized = STYLES[status] ? status : "idle";
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <motion.span
        aria-hidden
        className={cn("size-2 rounded-full", STYLES[normalized])}
        animate={normalized === "processing" ? { opacity: [1, 0.35, 1] } : { opacity: 1 }}
        transition={
          normalized === "processing"
            ? { duration: 1.6, repeat: Infinity, ease: "easeInOut" }
            : { duration: MOTION.fast }
        }
      />
      <span className="text-[13px] text-muted-foreground">{label ?? normalized}</span>
    </span>
  );
}
