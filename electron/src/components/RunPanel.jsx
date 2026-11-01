import { TriangleAlert } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert.jsx';
import { Badge } from '@/components/ui/badge.jsx';
import { Button } from '@/components/ui/button.jsx';
import { Separator } from '@/components/ui/separator.jsx';
import { Spinner } from '@/components/ui/spinner.jsx';
import StatusIndicator from '@/primitives/StatusIndicator.jsx';
import { STAGE_LABELS, stageDetailText, stageLabel } from '@/lib/worker-view.js';
import { cn } from 'cn';

const STATUS_LABELS = {
  processing: 'Processing',
  completed: 'Done',
  failed: 'Error',
  cancelled: 'Cancelled',
  stopped: 'Stopped',
};

function StageRow({ stage, info, active }) {
  const status = info?.status;
  const detail = stageDetailText(info?.detail);
  return (
    <li className="flex items-center justify-between gap-2 py-1.5">
      <span className="flex min-w-0 items-center gap-2 text-sm">
        {active ? <Spinner /> : null}
        <span className="flex min-w-0 flex-col">
          <span className="truncate text-[13px] text-foreground">{stageLabel(stage)}</span>
          {detail ? (
            <span className="truncate font-mono text-[11px] text-muted-foreground">
              {detail}
            </span>
          ) : null}
        </span>
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
  showActions = true,
  showStatus = true,
  className,
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
    <div className={cn("flex min-w-0 flex-col gap-3", className)}>
      {showStatus ? (
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
              {run.currentStage ? `${stageLabel(run.currentStage)} · ` : null}
              {run.completedCount}/{run.totalStages} stages
            </span>
          ) : null}
        </div>
      ) : null}

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
          {run.result.mode === 'extraction-only' ? (
            <p className="text-[13px] text-muted-foreground">
              Frame extraction succeeded. No AI analysis was performed —
              vision was skipped and this project has no observations to
              rebuild from.
            </p>
          ) : null}
          {run.result.mode === 'adaptive' ? (
        <p className="text-[13px] text-muted-foreground">
          Adaptive sampling finished. Counts below are measured from this
          run&apos;s artifacts.
        </p>
      ) : null}
      {run.result.mode === 'full' ? (
            <p className="text-[13px] text-muted-foreground">
              AI analysis complete. Counts below are measured from this
              run&apos;s artifacts.
            </p>
          ) : null}
          <ResultRows result={run.result} />
        </>
      ) : null}

      {run.status === 'failed' && run.error ? (
        <>
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertTitle>
            {run.error.stage && STAGE_LABELS[run.error.stage]
              ? stageLabel(run.error.stage)
              : 'Processing failed'}
          </AlertTitle>
          <AlertDescription className="min-w-0 [overflow-wrap:anywhere]">{run.error.message}</AlertDescription>
        </Alert>
        {Array.isArray(run.workerLogs) && run.workerLogs.length > 0 ? (
          <div className="flex min-w-0 flex-col gap-1">
            <p className="text-[11px] text-muted-foreground">Worker log (most recent last)</p>
            <pre className="max-h-32 min-w-0 overflow-auto rounded-sm border border-border bg-background p-2 font-mono text-[10px] leading-relaxed text-muted-foreground">
              {run.workerLogs.slice(-8).join("\n")}
            </pre>
          </div>
        ) : null}
        </>
      ) : null}

      {run.status === 'stopped' && run.result ? (
      <>
        <Separator />
        <p className="text-[13px] text-muted-foreground">
          Adaptive sampling stopped before completion. Validated
          observations and the run manifest are preserved; inspect them in
          Artifacts.
        </p>
        <ResultRows result={run.result} />
      </>
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
          <AlertDescription className="min-w-0 [overflow-wrap:anywhere]">{cancelError}</AlertDescription>
        </Alert>
      ) : null}

      {showActions ? <div className="flex flex-wrap gap-2">
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
      </div> : null}
    </div>
  );
}
