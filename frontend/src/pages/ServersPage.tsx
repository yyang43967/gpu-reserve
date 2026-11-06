import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Cpu, MemoryStick, Server as ServerIcon, ShieldCheck, Timer } from "lucide-react";
import { useEffect } from "react";
import { Link, useSearchParams } from "react-router";

import { useAuth } from "@/auth/auth-context";
import { Pagination } from "@/components/pagination";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { labsListKey, listLabs } from "@/labs/labs-api";
import type { GpuModel, Server, ServerStatus } from "@/lib/api-types";
import { pageCount } from "@/lib/pagination";
import {
  GPU_MODELS,
  SERVER_STATUS_LABELS,
  type ServerFilters,
  type ServerListParams,
  listServers,
  serversListKey,
} from "@/servers/servers-api";

const GPU_MODEL_VALUES = Object.keys(GPU_MODELS) as GpuModel[];
/** Distinct per-GPU memory sizes, smallest first. */
const MEMORY_OPTIONS = [...new Set(Object.values(GPU_MODELS).map((m) => m.memoryGb))].sort(
  (a, b) => a - b,
);
const MIN_GPU_OPTIONS = [2, 4, 8, 16];
const STUDENT_STATUSES: readonly ServerStatus[] = ["available", "maintenance"];
const ADMIN_STATUSES: readonly ServerStatus[] = ["available", "maintenance", "retired"];

const NO_FILTERS: ServerFilters = {
  labId: null,
  gpuModel: null,
  minGpuMemoryGb: null,
  minGpus: null,
  status: null,
};

function oneOf<T extends string | number>(value: string | null, allowed: readonly T[]): T | null {
  return allowed.find((item) => String(item) === value) ?? null;
}

/** Filters and page live in the URL, so reload, back, and shared links keep them. */
function useListParams(statuses: readonly ServerStatus[]) {
  const [searchParams, setSearchParams] = useSearchParams();
  const page = Number.parseInt(searchParams.get("page") ?? "", 10);
  const params: ServerListParams = {
    page: Number.isInteger(page) && page > 0 ? page : 1,
    // An empty value, as in "?lab=", means no filter.
    labId: searchParams.get("lab") ? searchParams.get("lab") : null,
    gpuModel: oneOf(searchParams.get("gpu"), GPU_MODEL_VALUES),
    minGpuMemoryGb: oneOf(searchParams.get("memory"), MEMORY_OPTIONS),
    minGpus: oneOf(searchParams.get("gpus"), MIN_GPU_OPTIONS),
    status: oneOf(searchParams.get("status"), statuses),
  };

  /** Paging adds a history entry so Back returns to the previous page; filters replace it. */
  const update = (changes: Partial<ServerListParams>, { push = false } = {}) => {
    // Any filter change starts again from the first page.
    const next = { ...params, page: 1, ...changes };
    const query = new URLSearchParams();
    if (next.page > 1) query.set("page", String(next.page));
    if (next.labId) query.set("lab", next.labId);
    if (next.gpuModel) query.set("gpu", next.gpuModel);
    if (next.minGpuMemoryGb !== null) query.set("memory", String(next.minGpuMemoryGb));
    if (next.minGpus !== null) query.set("gpus", String(next.minGpus));
    if (next.status) query.set("status", next.status);
    setSearchParams(query, { replace: !push });
  };

  return [params, update] as const;
}

function Filters({
  params,
  statuses,
  onChange,
}: {
  params: ServerListParams;
  statuses: readonly ServerStatus[];
  onChange: (changes: Partial<ServerFilters>) => void;
}) {
  const labs = useQuery({
    queryKey: labsListKey({ page: 1, isActive: null }),
    queryFn: ({ signal }) => listLabs({ page: 1, isActive: null }, signal),
  });
  const labIds = labs.data?.items.map((lab) => lab.id) ?? [];

  return (
    <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
      <div className="col-span-2 grid gap-2 md:col-span-1">
        <Label htmlFor="lab-filter">Lab</Label>
        <NativeSelect
          id="lab-filter"
          value={params.labId ?? ""}
          disabled={labs.isPending}
          onChange={(event) => {
            onChange({ labId: event.target.value || null });
          }}
        >
          <option value="">{labs.isError ? "All labs (list unavailable)" : "All labs"}</option>
          {labs.data?.items.map((lab) => (
            <option key={lab.id} value={lab.id}>
              {lab.is_active ? lab.name : `${lab.name} (inactive)`}
            </option>
          ))}
          {/* A lab from a shared link that this user cannot see still shows as selected. */}
          {params.labId && labs.isSuccess && !labIds.includes(params.labId) && (
            <option value={params.labId}>Unknown lab</option>
          )}
        </NativeSelect>
      </div>
      <div className="grid gap-2">
        <Label htmlFor="gpu-filter">GPU model</Label>
        <NativeSelect
          id="gpu-filter"
          value={params.gpuModel ?? ""}
          onChange={(event) => {
            onChange({ gpuModel: oneOf(event.target.value, GPU_MODEL_VALUES) });
          }}
        >
          <option value="">Any model</option>
          {GPU_MODEL_VALUES.map((model) => (
            <option key={model} value={model}>
              {GPU_MODELS[model].label}
            </option>
          ))}
        </NativeSelect>
      </div>
      <div className="grid gap-2">
        <Label htmlFor="memory-filter">Memory per GPU</Label>
        <NativeSelect
          id="memory-filter"
          value={params.minGpuMemoryGb ?? ""}
          onChange={(event) => {
            onChange({ minGpuMemoryGb: oneOf(event.target.value, MEMORY_OPTIONS) });
          }}
        >
          <option value="">Any memory</option>
          {MEMORY_OPTIONS.map((gb) => (
            <option key={gb} value={gb}>
              {gb} GB or more
            </option>
          ))}
        </NativeSelect>
      </div>
      <div className="grid gap-2">
        <Label htmlFor="gpus-filter">GPUs per server</Label>
        <NativeSelect
          id="gpus-filter"
          value={params.minGpus ?? ""}
          onChange={(event) => {
            onChange({ minGpus: oneOf(event.target.value, MIN_GPU_OPTIONS) });
          }}
        >
          <option value="">Any count</option>
          {MIN_GPU_OPTIONS.map((count) => (
            <option key={count} value={count}>
              {count} or more
            </option>
          ))}
        </NativeSelect>
      </div>
      <div className="grid gap-2">
        <Label htmlFor="status-filter">Status</Label>
        <NativeSelect
          id="status-filter"
          value={params.status ?? ""}
          onChange={(event) => {
            onChange({ status: oneOf(event.target.value, statuses) });
          }}
        >
          <option value="">Any status</option>
          {statuses.map((status) => (
            <option key={status} value={status}>
              {SERVER_STATUS_LABELS[status]}
            </option>
          ))}
        </NativeSelect>
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: ServerStatus }) {
  const variant =
    status === "available" ? "outline" : status === "retired" ? "destructive" : "secondary";
  return <Badge variant={variant}>{SERVER_STATUS_LABELS[status]}</Badge>;
}

function ServerCard({ server }: { server: Server }) {
  return (
    <li>
      <Link
        to={`/servers/${encodeURIComponent(server.id)}`}
        className="group flex h-full flex-col overflow-hidden rounded-lg border bg-card text-card-foreground shadow-xs transition-colors hover:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
      >
        <div className="flex aspect-[2/1] items-center justify-center bg-muted">
          {server.image_url ? (
            <img src={server.image_url} alt="" loading="lazy" className="size-full object-cover" />
          ) : (
            <ServerIcon className="size-10 text-muted-foreground" aria-hidden="true" />
          )}
        </div>
        <div className="flex flex-1 flex-col gap-3 p-4">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <h2 className="truncate font-semibold group-hover:underline">{server.hostname}</h2>
              <p className="truncate text-sm text-muted-foreground">{server.lab.name}</p>
            </div>
            <StatusBadge status={server.status} />
          </div>
          <p className="text-sm font-medium">
            {server.gpu_count} × {GPU_MODELS[server.gpu_model].label}
            <span className="font-normal text-muted-foreground">
              {" "}
              · {server.gpu_memory_gb} GB each
            </span>
          </p>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm text-muted-foreground">
            <div className="flex items-center gap-1.5">
              <dt>
                <Cpu className="size-4" aria-hidden="true" />
                <span className="sr-only">CPU cores</span>
              </dt>
              <dd>{server.cpu_cores} cores</dd>
            </div>
            <div className="flex items-center gap-1.5">
              <dt>
                <MemoryStick className="size-4" aria-hidden="true" />
                <span className="sr-only">RAM</span>
              </dt>
              <dd>{server.ram_gb} GB RAM</dd>
            </div>
            <div className="flex items-center gap-1.5">
              <dt>
                <Timer className="size-4" aria-hidden="true" />
                <span className="sr-only">Longest booking</span>
              </dt>
              <dd>Up to {server.max_booking_hours} h</dd>
            </div>
            {server.requires_approval && (
              <div className="flex items-center gap-1.5">
                <dt>
                  <ShieldCheck className="size-4" aria-hidden="true" />
                  <span className="sr-only">Booking</span>
                </dt>
                <dd>Needs approval</dd>
              </div>
            )}
          </dl>
        </div>
      </Link>
    </li>
  );
}

export function ServersPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const statuses = isAdmin ? ADMIN_STATUSES : STUDENT_STATUSES;
  const [params, updateParams] = useListParams(statuses);

  const servers = useQuery({
    queryKey: serversListKey(params),
    queryFn: ({ signal }) => listServers(params, signal),
    // Keep the current cards on screen while the next page or filter loads.
    placeholderData: keepPreviousData,
  });

  // A page past the end (a stale link, or servers removed since) steps back to the last one.
  const lastPage = servers.data ? pageCount(servers.data) : null;
  const pageTooHigh = lastPage !== null && !servers.isPlaceholderData && params.page > lastPage;
  useEffect(() => {
    if (pageTooHigh) updateParams({ page: lastPage });
  });

  const filtered =
    params.labId !== null ||
    params.gpuModel !== null ||
    params.minGpuMemoryGb !== null ||
    params.minGpus !== null ||
    params.status !== null;
  const clearFilters = () => {
    updateParams(NO_FILTERS);
  };

  let body;
  if (servers.isPending) {
    body = <LoadingState label="Loading servers" />;
  } else if (servers.isError) {
    body = (
      <ErrorState
        title="Could not load servers"
        message={servers.error.message}
        onRetry={() => {
          void servers.refetch();
        }}
      />
    );
  } else if (servers.data.items.length === 0) {
    body = filtered ? (
      <EmptyState
        title="No servers match these filters"
        description="Try fewer filters, or clear them."
        action={
          <Button variant="outline" size="sm" onClick={clearFilters}>
            Clear filters
          </Button>
        }
      />
    ) : (
      <EmptyState
        title="No servers yet"
        description={
          isAdmin
            ? "Add labs and servers from the admin area."
            : "No servers are open for booking yet. Check back later."
        }
      />
    );
  } else {
    body = (
      <>
        <ul
          aria-label="Servers"
          aria-busy={servers.isFetching}
          className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
        >
          {servers.data.items.map((server) => (
            <ServerCard key={server.id} server={server} />
          ))}
        </ul>
        <Pagination
          data={servers.data}
          onPage={(page) => {
            updateParams({ ...params, page }, { push: true });
          }}
        />
      </>
    );
  }

  return (
    <>
      <PageHeader title="Servers" description="Browse GPU servers across labs." />
      <Filters params={params} statuses={statuses} onChange={updateParams} />
      {body}
    </>
  );
}
