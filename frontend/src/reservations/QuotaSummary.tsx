import type { Quota } from "@/lib/api-types";
import { formatInLab } from "@/lib/lab-time";

/** GPU-hours used and left in one quota week, as a bar and numbers. */
export function QuotaSummary({
  quota,
  title = "GPU-hours this week",
}: {
  quota: Quota;
  title?: string;
}) {
  const { used_hours: used, quota_hours: total, remaining_hours: remaining } = quota;
  const percent = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 100;
  const weekStart = formatInLab(quota.week_start, quota.timezone);
  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-medium">{title}</h2>
        <p className="text-sm text-muted-foreground tabular-nums">
          {used} of {total} used · {remaining} left
        </p>
      </div>
      <div
        role="progressbar"
        aria-label="Weekly GPU-hours used"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={used}
        className="h-2 overflow-hidden rounded-full bg-muted"
      >
        <div className="h-full rounded-full bg-primary" style={{ width: `${String(percent)}%` }} />
      </div>
      <p className="text-xs text-muted-foreground">Week starting {weekStart}.</p>
    </div>
  );
}
