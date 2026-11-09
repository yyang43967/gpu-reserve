import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { formatInLab, formatLabRange } from "@/lib/lab-time";

import type { PendingAction, QuotaAfter } from "./assistant-api";

function quotaText(quota: QuotaAfter): string {
  return `${String(quota.used_gpu_hours)} of ${String(quota.quota_gpu_hours)} GPU-hours used that week, ${String(quota.remaining_gpu_hours)} left`;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[7rem_1fr] gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

export function ConfirmationCard({
  action,
  busy,
  onDecide,
}: {
  action: PendingAction;
  busy: boolean;
  onDecide: (decision: "confirm" | "cancel") => void;
}) {
  const booking = action.action === "create_reservation";
  const title = booking ? "Confirm this booking" : "Confirm this cancellation";
  return (
    <Card role="region" aria-label={title} className="border-primary/40">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>
          Nothing changes until you confirm. The assistant cannot do this on its own.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="space-y-1.5 text-sm">
          <Row label="Server">
            {action.summary.server} · {action.summary.lab}
          </Row>
          <Row label="GPUs">
            {action.summary.gpus}
            {booking && ` × ${action.summary.gpu_model.replaceAll("_", " ")}`}
          </Row>
          <Row label="Time">
            {formatLabRange(action.summary.start, action.summary.end, action.summary.timezone)}
          </Row>
          {booking ? (
            <>
              <Row label="Status">
                {action.summary.status_after === "pending"
                  ? "Needs an administrator's approval"
                  : "Approved immediately"}
              </Row>
              <Row label="Purpose">{action.summary.purpose}</Row>
            </>
          ) : (
            <Row label="Effect">
              {action.summary.effect === "end_early" && action.summary.new_end
                ? `Ends early at ${formatInLab(action.summary.new_end, action.summary.timezone)}`
                : "Cancelled before it starts"}
            </Row>
          )}
          <Row label="Quota after">{quotaText(action.summary.quota_after)}</Row>
        </dl>
      </CardContent>
      <CardFooter className="flex flex-wrap gap-2">
        <Button
          onClick={() => {
            onDecide("confirm");
          }}
          disabled={busy}
        >
          {booking ? "Confirm booking" : "Confirm cancellation"}
        </Button>
        <Button
          variant="outline"
          onClick={() => {
            onDecide("cancel");
          }}
          disabled={busy}
        >
          {booking ? "Don't book" : "Keep reservation"}
        </Button>
      </CardFooter>
    </Card>
  );
}
