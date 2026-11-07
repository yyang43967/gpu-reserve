import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { ServerUtilization, UtilizationTotals } from "@/lib/api-types";
import { labsListKey, listLabs } from "@/labs/labs-api";
import { GPU_MODELS, SERVER_STATUS_LABELS } from "@/servers/servers-api";
import { getUtilization, utilizationKey } from "@/stats/stats-api";

const HOUR_MS = 60 * 60_000;
const DAY_MS = 24 * HOUR_MS;
const LAB_QUERY = { page: 1, isActive: null } as const;

/** Ranges relative to now. Presets avoid asking which time zone typed dates are in. */
const RANGES = {
  "last-7": { label: "Last 7 days", from: -7, to: 0 },
  "last-30": { label: "Last 30 days", from: -30, to: 0 },
  "next-7": { label: "Next 7 days", from: 0, to: 7 },
  "next-14": { label: "Next 14 days", from: 0, to: 14 },
} as const;
type RangeKey = keyof typeof RANGES;
const RANGE_KEYS = Object.keys(RANGES) as RangeKey[];

function rangeWindow(key: RangeKey, now: Date): { from: string; to: string } {
  // Whole hours, so the query key stays the same for a while and results are cached.
  const hour = Math.floor(now.getTime() / HOUR_MS) * HOUR_MS;
  const range = RANGES[key];
  return {
    from: new Date(hour + range.from * DAY_MS).toISOString(),
    to: new Date(hour + range.to * DAY_MS).toISOString(),
  };
}

function percent(fraction: number): string {
  return `${(fraction * 100).toFixed(1)}%`;
}

function hours(value: number): string {
  return value.toLocaleString("en-US", { maximumFractionDigits: 1 });
}

/** Booked and pending as shares of available time. Decorative: the cells carry the numbers. */
function UsageBar({ row }: { row: UtilizationTotals }) {
  const total = row.available_gpu_hours || 1;
  const booked = Math.min(100, (row.booked_gpu_hours / total) * 100);
  const pending = Math.min(100 - booked, (row.pending_gpu_hours / total) * 100);
  return (
    <div
      aria-hidden="true"
      className="flex h-2.5 w-full min-w-24 overflow-hidden rounded-full bg-muted"
    >
      <div
        className="h-full bg-emerald-600 dark:bg-emerald-500"
        style={{ width: `${String(booked)}%` }}
      />
      <div
        className="h-full bg-amber-500 dark:bg-amber-400"
        style={{ width: `${String(pending)}%` }}
      />
    </div>
  );
}

function ServerRow({ row }: { row: ServerUtilization }) {
  return (
    <TableRow>
      <TableCell>
        <div className="font-medium">{row.hostname}</div>
        <div className="text-xs text-muted-foreground">
          {row.lab.name} · {row.gpu_count} × {GPU_MODELS[row.gpu_model].label}
          {row.status !== "available" && ` · ${SERVER_STATUS_LABELS[row.status]}`}
        </div>
      </TableCell>
      <TableCell className="w-1/3">
        <UsageBar row={row} />
      </TableCell>
      <TableCell className="text-right tabular-nums">{percent(row.utilization)}</TableCell>
      <TableCell className="text-right tabular-nums">{hours(row.booked_gpu_hours)}</TableCell>
      <TableCell className="text-right tabular-nums">{hours(row.pending_gpu_hours)}</TableCell>
      <TableCell className="text-right tabular-nums">{hours(row.available_gpu_hours)}</TableCell>
    </TableRow>
  );
}

export function AdminUtilizationPage() {
  const [range, setRange] = useState<RangeKey>("last-7");
  const [labId, setLabId] = useState<string | null>(null);
  const labs = useQuery({
    queryKey: labsListKey(LAB_QUERY),
    queryFn: ({ signal }) => listLabs(LAB_QUERY, signal),
  });
  const params = { labId, ...rangeWindow(range, new Date()) };
  const report = useQuery({
    queryKey: utilizationKey(params),
    queryFn: ({ signal }) => getUtilization(params, signal),
    placeholderData: keepPreviousData,
  });

  let body;
  if (report.isPending) {
    body = <LoadingState label="Loading utilization" />;
  } else if (report.isError) {
    body = (
      <ErrorState
        title="Could not load utilization"
        message={report.error.message}
        onRetry={() => {
          void report.refetch();
        }}
      />
    );
  } else if (report.data.servers.length === 0) {
    body = <EmptyState title="No servers" description="There are no servers in this selection." />;
  } else {
    const { totals } = report.data;
    body = (
      <div aria-busy={report.isFetching} className="overflow-x-auto rounded-xl border">
        <Table>
          <caption className="sr-only">
            GPU-hours per server for {RANGES[range].label.toLowerCase()}
          </caption>
          <TableHeader>
            <TableRow>
              <TableHead>Server</TableHead>
              <TableHead>Usage</TableHead>
              <TableHead className="text-right">Booked</TableHead>
              <TableHead className="text-right">Booked GPU-h</TableHead>
              <TableHead className="text-right">Pending GPU-h</TableHead>
              <TableHead className="text-right">Available GPU-h</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {report.data.servers.map((row) => (
              <ServerRow key={row.server_id} row={row} />
            ))}
          </TableBody>
          <tfoot className="border-t bg-muted/50 font-medium">
            <TableRow>
              <TableCell className="font-medium">All servers</TableCell>
              <TableCell>
                <UsageBar row={totals} />
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {percent(totals.utilization)}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {hours(totals.booked_gpu_hours)}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {hours(totals.pending_gpu_hours)}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {hours(totals.available_gpu_hours)}
              </TableCell>
            </TableRow>
          </tfoot>
        </Table>
      </div>
    );
  }

  return (
    <>
      <PageHeader
        title="Utilization"
        description="GPU-hours booked and pending against GPU-hours available, per server."
      />
      <div className="mb-4 grid gap-4 sm:grid-cols-2 lg:max-w-xl">
        <div className="grid gap-2">
          <Label htmlFor="utilization-range">Period</Label>
          <NativeSelect
            id="utilization-range"
            value={range}
            onChange={(event) => {
              setRange(RANGE_KEYS.find((key) => key === event.target.value) ?? "last-7");
            }}
          >
            {RANGE_KEYS.map((key) => (
              <option key={key} value={key}>
                {RANGES[key].label}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="utilization-lab">Lab</Label>
          <NativeSelect
            id="utilization-lab"
            value={labId ?? ""}
            disabled={!labs.data}
            onChange={(event) => {
              setLabId(event.target.value || null);
            }}
          >
            <option value="">All labs</option>
            {labs.data?.items.map((lab) => (
              <option key={lab.id} value={lab.id}>
                {lab.name}
              </option>
            ))}
          </NativeSelect>
        </div>
      </div>
      <p className="mb-3 flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="size-2.5 rounded-full bg-emerald-600 dark:bg-emerald-500"
          />
          Booked (approved and completed)
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="size-2.5 rounded-full bg-amber-500 dark:bg-amber-400"
          />
          Pending approval
        </span>
        <span>
          Retired servers are left out. Available time uses each server&apos;s current GPU count.
        </span>
      </p>
      {body}
    </>
  );
}
