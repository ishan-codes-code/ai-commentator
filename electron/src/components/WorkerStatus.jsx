import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card.jsx';
import StatusIndicator from '@/primitives/StatusIndicator.jsx';
import useWorkerStatus from '@/hooks/useWorkerStatus.js';

const LABELS = {
  idle: 'Idle',
  processing: 'Processing',
  done: 'Done',
  error: 'Error',
  cancelled: 'Cancelled',
};

export default function WorkerStatus() {
  const { status, detail } = useWorkerStatus();

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
