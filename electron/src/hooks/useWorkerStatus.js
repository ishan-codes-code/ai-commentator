import { useEffect, useState } from 'react';

const IDLE_DETAIL = 'No video processed yet.';

/**
 * Shared subscription to the worker bridge. Returns the coarse run
 * status plus the latest human-readable detail line. Second consumer
 * of the `WorkerStatus` subscription pattern (header pill + card).
 */
export default function useWorkerStatus() {
  const [status, setStatus] = useState('idle');
  const [detail, setDetail] = useState(IDLE_DETAIL);

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

  return { status, detail };
}
