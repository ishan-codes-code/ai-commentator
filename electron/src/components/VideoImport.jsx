import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import {
  Clapperboard,
  Film,
  FolderOpen,
  Play,
  TriangleAlert,
} from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert.jsx';
import { Badge } from '@/components/ui/badge.jsx';
import { Button } from '@/components/ui/button.jsx';
import { Spinner } from '@/components/ui/spinner.jsx';
import GlassCard from '@/primitives/GlassCard.jsx';
import {
  applyWorkerMessage,
  createInitialRunState,
} from '@/lib/worker-view.js';
import { extensionOf, fileNameOf, fileUrlOf, formatBytes } from '@/lib/format.js';
import { EASING, MOTION } from '@/lib/tokens.js';
import RunPanel from './RunPanel.jsx';

const SUPPORTED_NOTE = '.mp4 · .avi · .mov · .mkv · .webm';

/**
 * Video import + processing lifecycle for the Dashboard.
 *
 * Selection uses the audited `selectVideo` bridge (native OS dialog,
 * main-process validation). Processing uses `processVideo` with the
 * offline-safe `{ skipVision: true }` default; progress, cancellation,
 * and results flow through `onWorkerEvent`. No mock data anywhere.
 */
export default function VideoImport() {
  const [selection, setSelection] = useState(null);
  const [dialogBusy, setDialogBusy] = useState(false);
  const [selectError, setSelectError] = useState(null);
  const [previewFailed, setPreviewFailed] = useState(false);
  const [run, setRun] = useState(() => createInitialRunState());
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    if (typeof window.api?.onWorkerEvent !== 'function') {
      setSelectError('Worker bridge unavailable (running outside Electron?).');
      return undefined;
    }
    const unsubscribe = window.api.onWorkerEvent((message) => {
      if (!mountedRef.current) {
        return;
      }
      setRun((prev) => applyWorkerMessage(prev, message));
    });
    return () => {
      mountedRef.current = false;
      unsubscribe();
    };
  }, []);

  useEffect(() => {
    if (
      run.status === 'completed' ||
      run.status === 'failed' ||
      run.status === 'cancelled'
    ) {
      setCancelling(false);
    }
  }, [run.status]);

  const busy = dialogBusy || starting || run.status === 'processing';

  async function handleSelect() {
    if (dialogBusy || run.status === 'processing') {
      return;
    }
    setDialogBusy(true);
    setSelectError(null);
    try {
      if (typeof window.api?.selectVideo !== 'function') {
        throw new Error(
          'File selection is unavailable (worker bridge missing).',
        );
      }
      const res = await window.api.selectVideo();
      if (!mountedRef.current) {
        return;
      }
      if (!res) {
        throw new Error('No response from the file dialog.');
      }
      if (res.cancelled) {
        return;
      }
      if (res.ok !== true || !res.path) {
        throw new Error(res.error ?? 'Could not select a video.');
      }
      setSelection({ path: res.path, sizeBytes: res.sizeBytes ?? null });
      setPreviewFailed(false);
      setRun(createInitialRunState());
    } catch (err) {
      if (mountedRef.current) {
        setSelectError(err?.message ?? 'Could not select a video.');
      }
    } finally {
      if (mountedRef.current) {
        setDialogBusy(false);
      }
    }
  }

  async function handleStart() {
    if (
      !selection ||
      starting ||
      cancelling ||
      run.status === 'processing' ||
      !run.settled
    ) {
      return;
    }
    setStarting(true);
    setSelectError(null);
    try {
      if (typeof window.api?.processVideo !== 'function') {
        throw new Error('Worker bridge unavailable (running outside Electron?).');
      }
      const res = await window.api.processVideo(selection.path, {
        skipVision: true,
      });
      if (!mountedRef.current) {
        return;
      }
      if (!res || res.ok !== true) {
        setRun({
          ...createInitialRunState(),
          status: 'failed',
          settled: true,
          error: {
            stage: 'validation',
            message: res?.error ?? 'Could not start processing.',
          },
        });
      }
    } catch (err) {
      if (mountedRef.current) {
        setRun({
          ...createInitialRunState(),
          status: 'failed',
          settled: true,
          error: {
            stage: 'worker',
            message: err?.message ?? 'Could not start processing.',
          },
        });
      }
    } finally {
      if (mountedRef.current) {
        setStarting(false);
      }
    }
  }

  async function handleCancel() {
    if (cancelling || run.status !== 'processing') {
      return;
    }
    setCancelling(true);
    setCancelError(null);
    try {
      if (typeof window.api?.cancelProcessing !== 'function') {
        throw new Error('Worker bridge unavailable (running outside Electron?).');
      }
      await window.api.cancelProcessing();
    } catch (err) {
      if (mountedRef.current) {
        setCancelling(false);
        setCancelError(err?.message ?? 'Could not cancel processing.');
      }
    }
  }

  function handleRemove() {
    if (busy) {
      return;
    }
    setSelection(null);
    setPreviewFailed(false);
    setRun(createInitialRunState());
  }

  const phase = !selection
    ? 'empty'
    : run.status === 'idle'
      ? 'ready'
      : 'run';
  const fileUrl = selection ? fileUrlOf(selection.path) : '';

  return (
    <div className="flex flex-col gap-4">
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={phase}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: MOTION.standard, ease: EASING.entrance }}
        >
          {phase === 'empty' ? (
            <GlassCard className="flex flex-col items-center gap-3 p-8 text-center">
              <span className="flex size-11 items-center justify-center rounded-full bg-accent text-muted-foreground">
                <Clapperboard className="size-5" aria-hidden />
              </span>
              <h2 className="text-base font-semibold text-foreground">
                Import gameplay video
              </h2>
              <p className="max-w-sm text-sm text-muted-foreground">
                Choose a recorded match to extract frames and generate
                commentary. Supported formats: {SUPPORTED_NOTE}.
              </p>
              <Button
                type="button"
                variant="default"
                disabled={dialogBusy}
                onClick={handleSelect}
              >
                {dialogBusy ? (
                  <>
                    <Spinner data-icon="inline-start" />
                    Opening…
                  </>
                ) : (
                  <>
                    <FolderOpen data-icon="inline-start" />
                    Import gameplay video
                  </>
                )}
              </Button>
              {selectError ? (
                <Alert variant="destructive" className="max-w-sm text-left">
                  <TriangleAlert />
                  <AlertTitle>Could not open the file dialog</AlertTitle>
                  <AlertDescription>{selectError}</AlertDescription>
                </Alert>
              ) : null}
            </GlassCard>
          ) : null}

          {phase === 'ready' ? (
            <GlassCard className="flex flex-col gap-4 p-6">
              {fileUrl && !previewFailed ? (
                <video
                  key={selection.path}
                  src={fileUrl}
                  controls
                  preload="metadata"
                  onError={() => setPreviewFailed(true)}
                  className="aspect-video w-full rounded-card border border-border bg-background"
                >
                  <track kind="captions" />
                  Preview unavailable for this file.
                </video>
              ) : (
                <div className="flex flex-col items-center gap-2 rounded-card border border-border bg-background px-4 py-8 text-center">
                  <Film className="size-6 text-subtle" aria-hidden />
                  <p className="text-sm font-medium text-foreground">
                    Preview unavailable for this file
                  </p>
                  <p className="max-w-sm text-xs text-muted-foreground">
                    The desktop shell could not decode this video for
                    preview (codec or file access). Processing uses the
                    Python engine and is unaffected.
                  </p>
                </div>
              )}
              <div className="flex flex-col gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="min-w-0 flex-1 truncate font-mono text-sm text-foreground">
                    {fileNameOf(selection.path)}
                  </span>
                  <Badge variant="secondary">
                    .{extensionOf(selection.path)}
                  </Badge>
                  {selection.sizeBytes !== null ? (
                    <Badge variant="outline">
                      {formatBytes(selection.sizeBytes)}
                    </Badge>
                  ) : null}
                </div>
                <p className="truncate font-mono text-xs text-subtle">
                  {selection.path}
                </p>
                <p className="text-xs text-muted-foreground">
                  Default run is offline-safe: vision analysis is skipped,
                  so no API key is needed and no paid calls are made.
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="default"
                  disabled={starting}
                  onClick={handleStart}
                >
                  {starting ? (
                    <>
                      <Spinner data-icon="inline-start" />
                      Starting…
                    </>
                  ) : (
                    <>
                      <Play data-icon="inline-start" />
                      Start processing
                    </>
                  )}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy}
                  onClick={handleSelect}
                >
                  Replace
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  disabled={busy}
                  onClick={handleRemove}
                >
                  Remove
                </Button>
              </div>
              {selectError ? (
                <Alert variant="destructive">
                  <TriangleAlert />
                  <AlertTitle>Could not select a video</AlertTitle>
                  <AlertDescription>{selectError}</AlertDescription>
                </Alert>
              ) : null}
            </GlassCard>
          ) : null}

          {phase === 'run' ? (
            <div className="flex flex-col gap-4">
              <GlassCard className="flex flex-col gap-2 p-6">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="min-w-0 flex-1 truncate font-mono text-sm text-foreground">
                    {selection ? fileNameOf(selection.path) : 'Video'}
                  </span>
                  <Badge variant="secondary">
                    .{selection ? extensionOf(selection.path) : ''}
                  </Badge>
                </div>
                {starting ? (
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Spinner />
                    Starting worker…
                  </div>
                ) : null}
              </GlassCard>
              {!starting ? (
                <RunPanel
                  run={run}
                  cancelling={cancelling}
                  cancelError={cancelError}
                  onCancel={handleCancel}
                  onRetry={handleStart}
                  onChooseDifferent={handleSelect}
                />
              ) : null}
            </div>
          ) : null}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}
