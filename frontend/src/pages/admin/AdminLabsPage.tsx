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
import { labsKey, labsListKey, listLabs, type LabListParams } from "@/labs/labs-api";
import type { Lab } from "@/lib/api-types";
import { pageCount } from "@/lib/pagination";
import { useLatestSearchParams } from "@/lib/use-search-params";
import { cn } from "@/lib/utils";
import { CreateLabDialog, DeleteLabDialog, EditLabDialog } from "@/pages/admin/lab-dialogs";
import { serversKey } from "@/servers/servers-api";

type StatusFilter = "active" | "inactive";
const STATUSES: readonly StatusFilter[] = ["active", "inactive"];

const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

function readParams(searchParams: URLSearchParams): LabListParams {
  const page = Number.parseInt(searchParams.get("page") ?? "", 10);
  const status = STATUSES.find((item) => item === searchParams.get("status")) ?? null;
  return {
    page: Number.isInteger(page) && page > 0 ? page : 1,
    isActive: status === null ? null : status === "active",
  };
}

function writeParams(params: LabListParams): URLSearchParams {
  const query = new URLSearchParams();
  if (params.page > 1) query.set("page", String(params.page));
  if (params.isActive !== null) query.set("status", params.isActive ? "active" : "inactive");
  return query;
}

/** The status filter and page live in the URL, so reload, back, and shared links keep them. */
function useListParams() {
  const [searchParams, updateSearchParams] = useLatestSearchParams();
  const params = readParams(searchParams);

  /** Paging adds a history entry so Back returns to the previous page; filters replace it. */
  const update = (changes: Partial<LabListParams>, { push = false } = {}) => {
    updateSearchParams(
      // A filter change starts again from the first page.
      (current) => writeParams({ ...readParams(current), page: 1, ...changes }),
      { replace: !push },
    );
  };

  return [params, update] as const;
}

function LabsTable({
  labs,
  busy,
  onEdit,
  onDelete,
}: {
  labs: Lab[];
  busy: boolean;
  onEdit: (lab: Lab) => void;
  onDelete: (lab: Lab) => void;
}) {
  return (
    <div className="rounded-lg border" aria-busy={busy}>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Lab</TableHead>
            <TableHead className="hidden sm:table-cell">Time zone</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="hidden md:table-cell">Created</TableHead>
            <TableHead className="text-right">
              <span className="sr-only">Actions</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {labs.map((lab) => (
            <TableRow key={lab.id}>
              <TableCell className="whitespace-normal">
                <div className="font-medium">{lab.name}</div>
                <div className="text-muted-foreground">{lab.location}</div>
                {lab.description && (
                  <div className="mt-1 line-clamp-2 max-w-prose text-xs text-muted-foreground">
                    {lab.description}
                  </div>
                )}
              </TableCell>
              <TableCell className="hidden sm:table-cell">{lab.timezone}</TableCell>
              <TableCell>
                <Badge variant={lab.is_active ? "outline" : "secondary"}>
                  {lab.is_active ? "Active" : "Inactive"}
                </Badge>
              </TableCell>
              <TableCell className="hidden md:table-cell">
                {dateFormat.format(new Date(lab.created_at))}
              </TableCell>
              <TableCell className="text-right">
                <div className="flex justify-end gap-1">
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Edit ${lab.name}`}
                    onClick={() => {
                      onEdit(lab);
                    }}
                  >
                    <Pencil aria-hidden="true" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Delete ${lab.name}`}
                    onClick={() => {
                      onDelete(lab);
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

export function AdminLabsPage() {
  const queryClient = useQueryClient();
  const [params, updateParams] = useListParams();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Lab | null>(null);
  const [deleting, setDeleting] = useState<Lab | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const labs = useQuery({
    queryKey: labsListKey(params),
    queryFn: ({ signal }) => listLabs(params, signal),
    placeholderData: keepPreviousData,
  });

  // Deleting the last row of the last page leaves an empty page: step back to one that exists.
  const lastPage = labs.data ? pageCount(labs.data) : null;
  const pageTooHigh = lastPage !== null && !labs.isPlaceholderData && params.page > lastPage;
  useEffect(() => {
    if (pageTooHigh) updateParams({ page: lastPage });
  });

  // Servers embed their lab's name and status, so they are refreshed too.
  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: labsKey }),
      queryClient.invalidateQueries({ queryKey: serversKey }),
    ]);

  const filtered = params.isActive !== null;

  let body;
  if (labs.isPending) {
    body = <LoadingState label="Loading labs" />;
  } else if (labs.isError) {
    body = (
      <ErrorState
        title="Could not load labs"
        message={labs.error.message}
        onRetry={() => {
          void labs.refetch();
        }}
      />
    );
  } else if (labs.data.items.length === 0) {
    body = filtered ? (
      <EmptyState
        title={params.isActive ? "No active labs" : "No inactive labs"}
        action={
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              updateParams({ isActive: null });
            }}
          >
            Show all labs
          </Button>
        }
      />
    ) : (
      <EmptyState title="No labs yet" description="Create a lab, then add its servers." />
    );
  } else {
    body = (
      <>
        <LabsTable
          labs={labs.data.items}
          busy={labs.isFetching}
          onEdit={(lab) => {
            setNotice(null);
            setEditing(lab);
          }}
          onDelete={(lab) => {
            setNotice(null);
            setDeleting(lab);
          }}
        />
        <Pagination
          data={labs.data}
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
        title="Labs"
        description="Where servers live, and the time zone their bookings use."
        actions={
          <Button
            onClick={() => {
              setNotice(null);
              setCreating(true);
            }}
          >
            <Plus aria-hidden="true" />
            New lab
          </Button>
        }
      />
      <div className="mb-4 grid gap-2 sm:w-48">
        <Label htmlFor="lab-status-filter">Status</Label>
        <NativeSelect
          id="lab-status-filter"
          value={params.isActive === null ? "" : params.isActive ? "active" : "inactive"}
          onChange={(event) => {
            const value = event.target.value;
            setNotice(null);
            updateParams({ isActive: value === "" ? null : value === "active" });
          }}
        >
          <option value="">Any status</option>
          <option value="active">Active</option>
          <option value="inactive">Inactive</option>
        </NativeSelect>
      </div>
      {/* Always rendered so screen readers announce the text when it appears. */}
      <p role="status" className={cn("text-sm text-muted-foreground", notice && "mb-4")}>
        {notice}
      </p>
      {body}

      <CreateLabDialog
        open={creating}
        onOpenChange={setCreating}
        onCreated={(lab) => {
          setCreating(false);
          setNotice(`Created ${lab.name}.`);
          void refresh();
        }}
      />
      <EditLabDialog
        lab={editing}
        onOpenChange={(open) => {
          if (!open) setEditing(null);
        }}
        onSaved={(lab) => {
          setEditing(null);
          setNotice(`Saved changes to ${lab.name}.`);
          void refresh();
        }}
      />
      <DeleteLabDialog
        lab={deleting}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        onDeleted={(lab) => {
          setDeleting(null);
          setNotice(`Deleted ${lab.name}.`);
          void refresh();
        }}
        onDeactivated={(lab) => {
          setDeleting(null);
          setNotice(`Deactivated ${lab.name}.`);
          void refresh();
        }}
      />
    </>
  );
}
