import { TriangleAlert } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert.jsx';
import { Badge } from '@/components/ui/badge.jsx';
import { Button } from '@/components/ui/button.jsx';
import { Separator } from '@/components/ui/separator.jsx';
import { Spinner } from '@/components/ui/spinner.jsx';
import GlassCard from '@/primitives/GlassCard.jsx';
import StatusIndicator from '@/primitives/StatusIndicator.jsx';
import { STAGE_LABELS, stageLabel } from '@/lib/worker-view.js';

const STATUS_LABELS = {
  processing: 'Processing',
  completed: 'Done',
  failed: 'Error',
  cancelled: 'Cancelled',
};

function StageRow({ stage, info, active }) {
  const status = info?.status;
  return (
    <li className="flex items-center justify-between gap-2 py-1.5">
      <span className="flex min-w-0 items-center gap-2 text-sm">
        {active ? <Spinner /> : null}
        <span className="truncate text-[13px] text-foreground">{stageLabel(stage)}</span>
      </span>
      {status === 'done' ? (
        <Badge variant="secondary">Done</Badge>
      ) : status === 'skipped' ? (
        <Badge variant="outline">Skipped</Badge>
      ) : active ? (
        <Badge variant="default">Active</Badge>
      ) : null}
    </li>
  );
}

function ResultRows({ result }) {
  const rows = [
    ['Frames', result.frames],
    ['Observations', result.observations],
    ['Events', result.events],
    ['Commentary lines', result.commentary_lines],
    ['Script', result.script],
  ].filter(([, value]) => value !== null && value !== undefined);
  return (
    <dl className="flex flex-col gap-1.5">
      {rows.map(([label, value]) => (
        <div key={label} className="flex items-baseline justify-between gap-4">
          <dt className="text-[13px] text-muted-foreground">{label}</dt>
          <dd className="font-mono text-[13px] text-foreground">{String(value)}</dd>
        </div>
      ))}
      {result.output_dir ? (
        <div className="flex flex-col gap-1 pt-1">
          <dt className="text-xs text-subtle">Output directory</dt>
          <dd className="truncate font-mono text-xs text-muted-foreground">
            {result.output_dir}
          </dd>
        </div>
      ) : null}
    </dl>
  );
}

/**
 * Presentational run states: progress, cancelling, result, error,
 * cancelled. All values come from the worker-view mapper; nothing here
 * invents stages, counts, or commentary.
 */
export default function RunPanel({
  run,
  cancelling,
  cancelError,
  onCancel,
  onRetry,
  onChooseDifferent,
  chooseDifferentLabel = "Choose different video",
}) {
  const indicatorStatus =
    run.status === 'completed'
      ? 'done'
      : run.status === 'failed'
        ? 'error'
        : run.status;
  const seenStages = Object.keys(run.stages);
  const orderedStages = [
    ...seenStages,
    ...(run.currentStage && !seenStages.includes(run.currentStage)
      ? [run.currentStage]
      : []),
  ];

  return (
    <GlassCard className="flex flex-col gap-4 p-5">
      <div
        aria-live="polite"
        className="flex flex-wrap items-center justify-between gap-2"
      >
        <StatusIndicator
          status={indicatorStatus}
          label={STATUS_LABELS[run.status] ?? run.status}
        />
        {run.totalStages > 0 ? (
          <span className="font-mono text-xs text-muted-foreground">
            {run.currentStage
              ? `${stageLabel(run.currentStage)} · `
              : null}
            {run.completedCount}/{run.totalStages} stages
          </span>
        ) : null}
      </div>

      {orderedStages.length > 0 ? (
        <>
          <Separator />
          <ul className="flex flex-col divide-y divide-border">
            {orderedStages.map((stage) => (
              <StageRow
                key={stage}
                stage={stage}
                info={run.stages[stage]}
                active={run.currentStage === stage}
              />
            ))}
          </ul>
        </>
      ) : null}

      {run.status === 'processing' && orderedStages.length === 0 ? (
        <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
          <Spinner />
          {cancelling ? 'Cancelling…' : 'Starting worker…'}
        </div>
      ) : null}

      {run.status === 'completed' && run.result ? (
        <>
          <Separator />
          <ResultRows result={run.result} />
        </>
      ) : null}

      {run.status === 'failed' && run.error ? (
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertTitle>
            {run.error.stage && STAGE_LABELS[run.error.stage]
              ? stageLabel(run.error.stage)
              : 'Processing failed'}
          </AlertTitle>
          <AlertDescription>{run.error.message}</AlertDescription>
        </Alert>
      ) : null}

      {run.status === 'cancelled' ? (
        <p className="text-[13px] text-muted-foreground">
          Processing was cancelled. No partial results are shown; start
          again when ready.
        </p>
      ) : null}

      {cancelError ? (
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertTitle>Could not cancel</AlertTitle>
          <AlertDescription>{cancelError}</AlertDescription>
        </Alert>
      ) : null}

      <div className="flex flex-wrap gap-2">
        {run.status === 'processing' ? (
          <Button
            type="button"
            variant="outline"
            disabled={cancelling}
            onClick={onCancel}
          >
            {cancelling ? (
              <>
                <Spinner data-icon="inline-start" />
                Cancelling…
              </>
            ) : (
              'Cancel processing'
            )}
          </Button>
        ) : null}
        {(run.status === 'completed' ||
          run.status === 'failed' ||
          run.status === 'cancelled') &&
        run.settled ? (
          <>
            <Button type="button" variant="default" onClick={onRetry}>
              Process again
            </Button>
            <Button type="button" variant="outline" onClick={onChooseDifferent}>
              {chooseDifferentLabel}
            </Button>
          </>
        ) : null}
      </div>
    </GlassCard>
  );
}
