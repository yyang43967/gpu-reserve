import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, Pencil, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { Pagination } from "@/components/pagination";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { labsListKey, listLabs } from "@/labs/labs-api";
import type { Server, ServerStatus } from "@/lib/api-types";
import { pageCount } from "@/lib/pagination";
import { useLatestSearchParams } from "@/lib/use-search-params";
import { cn } from "@/lib/utils";
import {
  CreateServerDialog,
  DeleteServerDialog,
  EditServerDialog,
} from "@/pages/admin/server-dialogs";
import {
  GPU_MODELS,
  listServers,
  SERVER_STATUS_LABELS,
  serversKey,
  serversListKey,
  type ServerListParams,
} from "@/servers/servers-api";

const STATUSES = Object.keys(SERVER_STATUS_LABELS) as ServerStatus[];
const ALL_LABS = { page: 1, isActive: null } as const;

function readParams(searchParams: URLSearchParams): ServerListParams {
  const page = Number.parseInt(searchParams.get("page") ?? "", 10);
  return {
    page: Number.isInteger(page) && page > 0 ? page : 1,
    // An empty value, as in "?lab=", means no filter.
    labId: searchParams.get("lab") ? searchParams.get("lab") : null,
    gpuModel: null,
    minGpuMemoryGb: null,
    minGpus: null,
    status: STATUSES.find((status) => status === searchParams.get("status")) ?? null,
  };
}

function writeParams(params: ServerListParams): URLSearchParams {
  const query = new URLSearchParams();
  if (params.page > 1) query.set("page", String(params.page));
  if (params.labId) query.set("lab", params.labId);
  if (params.status) query.set("status", params.status);
  return query;
}

/** Filters and page live in the URL, so reload, back, and shared links keep them. */
function useListParams() {
  const [searchParams, updateSearchParams] = useLatestSearchParams();
  const params = readParams(searchParams);

  /** Paging adds a history entry so Back returns to the previous page; filters replace it. */
  const update = (changes: Partial<ServerListParams>, { push = false } = {}) => {
    updateSearchParams(
      // Any filter change starts again from the first page.
      (current) => writeParams({ ...readParams(current), page: 1, ...changes }),
      { replace: !push },
    );
  };

  return [params, update] as const;
}

const STATUS_VARIANTS = {
  available: "outline",
  maintenance: "secondary",
  retired: "destructive",
} as const;

function ServersTable({
  servers,
  busy,
  onEdit,
  onDelete,
}: {
  servers: Server[];
  busy: boolean;
  onEdit: (server: Server) => void;
  onDelete: (server: Server) => void;
}) {
  return (
    <div className="rounded-lg border" aria-busy={busy}>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Server</TableHead>
            <TableHead className="hidden sm:table-cell">GPUs</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="hidden text-right md:table-cell">Longest booking</TableHead>
            <TableHead className="text-right">
              <span className="sr-only">Actions</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {servers.map((server) => (
            <TableRow key={server.id}>
              <TableCell className="whitespace-normal">
                <Link
                  to={`/servers/${encodeURIComponent(server.id)}`}
                  className="font-medium hover:underline"
                >
                  {server.hostname}
                </Link>
                <div className="text-muted-foreground">
                  {server.lab.name}
                  {!server.lab.is_active && " (inactive)"}
                </div>
              </TableCell>
              <TableCell className="hidden sm:table-cell">
                {server.gpu_count} × {GPU_MODELS[server.gpu_model].label}
              </TableCell>
              <TableCell>
                <div className="flex flex-col items-start gap-1 sm:flex-row">
                  <Badge variant={STATUS_VARIANTS[server.status]}>
                    {SERVER_STATUS_LABELS[server.status]}
                  </Badge>
                  {server.requires_approval && <Badge variant="secondary">Approval</Badge>}
                </div>
              </TableCell>
              <TableCell className="hidden text-right tabular-nums md:table-cell">
                {server.max_booking_hours} h
              </TableCell>
              <TableCell className="text-right">
                <div className="flex justify-end gap-1">
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Edit ${server.hostname}`}
                    onClick={() => {
                      onEdit(server);
                    }}
                  >
                    <Pencil aria-hidden="true" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Delete ${server.hostname}`}
                    onClick={() => {
                      onDelete(server);
                    }}
                  >
                    <Trash2 aria-hidden="true" />
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

export function AdminServersPage() {
  const queryClient = useQueryClient();
  const [params, updateParams] = useListParams();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Server | null>(null);
  const [deleting, setDeleting] = useState<Server | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const labs = useQuery({
    queryKey: labsListKey(ALL_LABS),
    queryFn: ({ signal }) => listLabs(ALL_LABS, signal),
  });
  const labList = labs.data?.items ?? [];
  const hasActiveLab = labList.some((lab) => lab.is_active);

  const servers = useQuery({
    queryKey: serversListKey(params),
    queryFn: ({ signal }) => listServers(params, signal),
    placeholderData: keepPreviousData,
  });

  // Deleting the last row of the last page leaves an empty page: step back to one that exists.
  const lastPage = servers.data ? pageCount(servers.data) : null;
  const pageTooHigh = lastPage !== null && !servers.isPlaceholderData && params.page > lastPage;
  useEffect(() => {
    if (pageTooHigh) updateParams({ page: lastPage });
  });

  // Covers the list, the student pages, and each server's detail page.
  const refresh = () => queryClient.invalidateQueries({ queryKey: serversKey });

  const filtered = params.labId !== null || params.status !== null;

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
        action={
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              updateParams({ labId: null, status: null });
            }}
          >
            Clear filters
          </Button>
        }
      />
    ) : (
      <EmptyState
        title="No servers yet"
        description={
          labs.isSuccess && !hasActiveLab
            ? "Create an active lab first, then add its servers."
            : "Add the first server to start taking bookings."
        }
        action={
          labs.isSuccess && !hasActiveLab ? (
            <Link to="/admin/labs" className="text-sm underline underline-offset-4">
              Go to labs
            </Link>
          ) : undefined
        }
      />
    );
  } else {
    body = (
      <>
        <ServersTable
          servers={servers.data.items}
          busy={servers.isFetching}
          onEdit={(server) => {
            setNotice(null);
            setEditing(server);
          }}
          onDelete={(server) => {
            setNotice(null);
            setDeleting(server);
          }}
        />
        <Pagination
          data={servers.data}
          onPage={(page) => {
            updateParams({ page }, { push: true });
          }}
        />
      </>
    );
  }

  return (
    <>
      <Link
        to="/admin"
        className="mb-2 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ChevronLeft className="size-4" aria-hidden="true" />
        Admin
      </Link>
      <PageHeader
        title="Servers"
        description="Hardware, booking rules, and photos for each server."
        actions={
          <Button
            disabled={!hasActiveLab}
            title={labs.isSuccess && !hasActiveLab ? "Create an active lab first" : undefined}
            onClick={() => {
              setNotice(null);
              setCreating(true);
            }}
          >
            <Plus aria-hidden="true" />
            New server
          </Button>
        }
      />
      <div className="mb-4 grid grid-cols-2 gap-3 sm:w-96">
        <div className="grid gap-2">
          <Label htmlFor="admin-server-lab">Lab</Label>
          <NativeSelect
            id="admin-server-lab"
            value={params.labId ?? ""}
            disabled={labs.isPending}
            onChange={(event) => {
              setNotice(null);
              updateParams({ labId: event.target.value === "" ? null : event.target.value });
            }}
          >
            <option value="">All labs</option>
            {labList.map((lab) => (
              <option key={lab.id} value={lab.id}>
                {lab.is_active ? lab.name : `${lab.name} (inactive)`}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="admin-server-status">Status</Label>
          <NativeSelect
            id="admin-server-status"
            value={params.status ?? ""}
            onChange={(event) => {
              setNotice(null);
              updateParams({
                status: STATUSES.find((status) => status === event.target.value) ?? null,
              });
            }}
          >
            <option value="">Any status</option>
            {STATUSES.map((status) => (
              <option key={status} value={status}>
                {SERVER_STATUS_LABELS[status]}
              </option>
            ))}
          </NativeSelect>
        </div>
      </div>
      {/* Always rendered so screen readers announce the text when it appears. */}
      <p role="status" className={cn("text-sm text-muted-foreground", notice && "mb-4")}>
        {notice}
      </p>
      {body}

      <CreateServerDialog
        open={creating}
        labs={labList}
        onOpenChange={setCreating}
        onCreated={(server) => {
          setCreating(false);
          setNotice(`Created ${server.hostname}.`);
          void refresh();
        }}
      />
      <EditServerDialog
        server={editing}
        labs={labList}
        onOpenChange={(open) => {
          if (!open) setEditing(null);
        }}
        onSaved={(server) => {
          setEditing(null);
          setNotice(`Saved changes to ${server.hostname}.`);
          void refresh();
        }}
      />
      <DeleteServerDialog
        server={deleting}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        onDeleted={(server) => {
          setDeleting(null);
          setNotice(`Deleted ${server.hostname}.`);
          void refresh();
        }}
      />
    </>
  );
}
