import { Separator } from '@/components/ui/separator.jsx';
import StatusIndicator from '@/primitives/StatusIndicator.jsx';
import useWorkerStatus from '@/hooks/useWorkerStatus.js';

const LABELS = {
  idle: 'Idle',
  processing: 'Processing',
  done: 'Done',
  error: 'Error',
  cancelled: 'Cancelled',
};

/**
 * Slim application header: current-section context on the left,
 * live engine status on the right. A bar, not a panel — flat surface
 * with a bottom hairline, no shadow, no glass.
 */
export default function AppHeader({ title, context }) {
  const { status } = useWorkerStatus();

  return (
    <header className="flex h-11 shrink-0 items-center justify-between gap-4 border-b border-border bg-surface px-4">
      <div className="flex min-w-0 items-baseline gap-2">
        <h1 className="truncate text-[13px] font-semibold tracking-[-0.01em] text-foreground">
          {title}
        </h1>
        {context ? (
          <span className="hidden truncate text-[11px] text-subtle sm:inline">
            {context}
          </span>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <span className="hidden text-xs text-subtle md:inline">
          Engine
        </span>
        <Separator orientation="vertical" className="hidden h-4 md:block" />
        <StatusIndicator status={status} label={LABELS[status] ?? status} />
      </div>
    </header>
  );
}
