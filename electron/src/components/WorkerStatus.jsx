import { useEffect, useState } from 'react';
import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card.jsx';
import StatusIndicator from '@/primitives/StatusIndicator.jsx';

const LABELS = {
  idle: 'Idle',
  processing: 'Processing',
  done: 'Done',
  error: 'Error',
  cancelled: 'Cancelled',
};

export default function WorkerStatus() {
  const [status, setStatus] = useState('idle');
  const [detail, setDetail] = useState('No video processed yet.');

  useEffect(() => {
    if (!window.api?.onWorkerEvent) {
      setDetail('Worker bridge unavailable (running outside Electron?).');
      return undefined;
    }
    const unsubscribe = window.api.onWorkerEvent((message) => {
      if (!message || typeof message.type !== 'string') {
        return;
      }
      if (message.type === 'started') {
        setStatus('processing');
        setDetail(message.message ?? 'Processing video…');
      } else if (message.type === 'progress') {
        setDetail(
          `Stage ${message.stage}: ${message.completed}/${message.total}`,
        );
      } else if (message.type === 'stage_completed') {
        setDetail(`Completed stage ${message.stage}.`);
      } else if (message.type === 'result') {
        setStatus('done');
        setDetail(
          `Done: ${message.data?.frames ?? '?'} frames, ` +
            `${message.data?.commentary_lines ?? '?'} commentary lines.`,
        );
      } else if (message.type === 'error') {
        setStatus('error');
        setDetail(`Error (${message.stage}): ${message.message}`);
      } else if (message.type === 'worker-exit') {
        if (message.cancelled) {
          setStatus('cancelled');
          setDetail('Processing was cancelled.');
        } else {
          setDetail(`Worker exited with code ${message.code}.`);
        }
      }
    });
    return unsubscribe;
  }, []);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">Worker</CardTitle>
        <CardAction>
          <StatusIndicator status={status} label={LABELS[status] ?? status} />
        </CardAction>
      </CardHeader>
      <CardContent>
        <p className="truncate font-mono text-xs text-muted-foreground">
          {detail}
        </p>
      </CardContent>
    </Card>
  );
}
