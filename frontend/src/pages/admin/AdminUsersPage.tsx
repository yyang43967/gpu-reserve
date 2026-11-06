import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { useEffect, useState, type SyntheticEvent } from "react";
import { Link, useSearchParams } from "react-router";

import {
  adminUsersKey,
  adminUsersListKey,
  listUsers,
  type StatusFilter,
  type UserListParams,
} from "@/admin/users-api";
import { meQueryKey, useAuth } from "@/auth/auth-context";
import { PageHeader } from "@/components/page-header";
import { Pagination } from "@/components/pagination";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
import type { User, UserRole } from "@/lib/api-types";
import { pageCount } from "@/lib/pagination";
import { cn } from "@/lib/utils";
import { CreateUserDialog, DeleteUserDialog, EditUserDialog } from "@/pages/admin/user-dialogs";

const ROLES: readonly UserRole[] = ["student", "admin"];
const STATUSES: readonly StatusFilter[] = ["active", "inactive"];

const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

function oneOf<T extends string>(value: string | null, allowed: readonly T[]): T | null {
  return allowed.find((item) => item === value) ?? null;
}

/** Filters and page live in the URL, so reload, back, and shared links keep them. */
function useListParams() {
  const [searchParams, setSearchParams] = useSearchParams();
  const page = Number.parseInt(searchParams.get("page") ?? "", 10);
  const params: UserListParams = {
    page: Number.isInteger(page) && page > 0 ? page : 1,
    q: searchParams.get("q")?.trim() ?? "",
    role: oneOf(searchParams.get("role"), ROLES),
    status: oneOf(searchParams.get("status"), STATUSES),
  };

  /** Paging adds a history entry so Back returns to the previous page; filters replace it. */
  const update = (changes: Partial<UserListParams>, { push = false } = {}) => {
    // Any filter change starts again from the first page.
    const next = { ...params, page: 1, ...changes };
    const query = new URLSearchParams();
    if (next.page > 1) query.set("page", String(next.page));
    if (next.q) query.set("q", next.q);
    if (next.role) query.set("role", next.role);
    if (next.status) query.set("status", next.status);
    setSearchParams(query, { replace: !push });
  };

  return [params, update] as const;
}

function Filters({
  params,
  onChange,
}: {
  params: UserListParams;
  onChange: (changes: Partial<UserListParams>) => void;
}) {
  const [search, setSearch] = useState(params.q);
  // Keep the box in step with the URL when it changes elsewhere (back button, clear).
  const [syncedQ, setSyncedQ] = useState(params.q);
  if (syncedQ !== params.q) {
    setSyncedQ(params.q);
    setSearch(params.q);
  }

  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    onChange({ q: search.trim() });
  };

  return (
    <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-end">
      <form role="search" onSubmit={submit} className="grid flex-1 gap-2">
        <Label htmlFor="user-search">Search</Label>
        <div className="flex gap-2">
          <Input
            id="user-search"
            type="search"
            placeholder="Name or email"
            maxLength={100}
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
            }}
          />
          <Button type="submit" variant="outline">
            <Search aria-hidden="true" />
            Search
          </Button>
        </div>
      </form>
      <div className="grid grid-cols-2 gap-3 md:w-80">
        <div className="grid gap-2">
          <Label htmlFor="role-filter">Role</Label>
          <NativeSelect
            id="role-filter"
            value={params.role ?? ""}
            onChange={(event) => {
              onChange({ role: oneOf(event.target.value, ROLES) });
            }}
          >
            <option value="">All roles</option>
            <option value="student">Students</option>
            <option value="admin">Admins</option>
          </NativeSelect>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="status-filter">Status</Label>
          <NativeSelect
            id="status-filter"
            value={params.status ?? ""}
            onChange={(event) => {
              onChange({ status: oneOf(event.target.value, STATUSES) });
            }}
          >
            <option value="">Any status</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </NativeSelect>
        </div>
      </div>
    </div>
  );
}

/** An email that wraps before the "@" on narrow screens instead of mid-word. */
function EmailText({ email }: { email: string }) {
  const at = email.lastIndexOf("@");
  if (at <= 0) return email;
  return (
    <>
      {email.slice(0, at)}
      <wbr />
      {email.slice(at)}
    </>
  );
}

function UsersTable({
  users,
  currentUserId,
  busy,
  onEdit,
  onDelete,
}: {
  users: User[];
  currentUserId: string;
  busy: boolean;
  onEdit: (user: User) => void;
  onDelete: (user: User) => void;
}) {
  return (
    <div className="rounded-lg border" aria-busy={busy}>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>User</TableHead>
            <TableHead>Access</TableHead>
            <TableHead className="hidden text-right sm:table-cell">Quota (h/week)</TableHead>
            <TableHead className="hidden md:table-cell">Created</TableHead>
            <TableHead className="text-right">
              <span className="sr-only">Actions</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {users.map((user) => {
            const isSelf = user.id === currentUserId;
            return (
              <TableRow key={user.id}>
                <TableCell className="whitespace-normal">
                  <div className="font-medium">
                    {user.full_name}
                    {isSelf && <span className="font-normal text-muted-foreground"> (you)</span>}
                  </div>
                  <div className="[overflow-wrap:anywhere] text-muted-foreground">
                    <EmailText email={user.email} />
                  </div>
                </TableCell>
                <TableCell>
                  {/* Stacked on phones so the email column keeps its width. */}
                  <div className="flex flex-col items-start gap-1 sm:flex-row">
                    <Badge variant={user.role === "admin" ? "default" : "secondary"}>
                      {user.role === "admin" ? "Admin" : "Student"}
                    </Badge>
                    <Badge variant={user.is_active ? "outline" : "destructive"}>
                      {user.is_active ? "Active" : "Inactive"}
                    </Badge>
                  </div>
                </TableCell>
                <TableCell className="hidden text-right tabular-nums sm:table-cell">
                  {user.weekly_gpu_hour_quota}
                </TableCell>
                <TableCell className="hidden md:table-cell">
                  {dateFormat.format(new Date(user.created_at))}
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-1">
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Edit ${user.full_name}`}
                      onClick={() => {
                        onEdit(user);
                      }}
                    >
                      <Pencil aria-hidden="true" />
                    </Button>
                    {/* Admins cannot delete themselves; the API refuses it as well. */}
                    {!isSelf && (
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Delete ${user.full_name}`}
                        onClick={() => {
                          onDelete(user);
                        }}
                      >
                        <Trash2 aria-hidden="true" />
                      </Button>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

export function AdminUsersPage() {
  const { user: currentUser } = useAuth();
  const queryClient = useQueryClient();
  const [params, updateParams] = useListParams();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<User | null>(null);
  const [deleting, setDeleting] = useState<User | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const users = useQuery({
    queryKey: adminUsersListKey(params),
    queryFn: ({ signal }) => listUsers(params, signal),
    // Keep the current rows on screen while the next page or filter loads.
    placeholderData: keepPreviousData,
  });

  // Deleting the last row of the last page leaves an empty page: step back to one that exists.
  const lastPage = users.data ? pageCount(users.data) : null;
  const pageTooHigh = lastPage !== null && !users.isPlaceholderData && params.page > lastPage;
  useEffect(() => {
    if (pageTooHigh) updateParams({ page: lastPage });
  });

  const refreshList = () => queryClient.invalidateQueries({ queryKey: adminUsersKey });

  const filtered = params.q !== "" || params.role !== null || params.status !== null;
  const clearFilters = () => {
    updateParams({ q: "", role: null, status: null });
  };

  let body;
  if (users.isPending) {
    body = <LoadingState label="Loading users" />;
  } else if (users.isError) {
    body = (
      <ErrorState
        title="Could not load users"
        message={users.error.message}
        onRetry={() => {
          void users.refetch();
        }}
      />
    );
  } else if (users.data.items.length === 0) {
    body = filtered ? (
      <EmptyState
        title="No users match these filters"
        description="Try a different search, or clear the filters."
        action={
          <Button variant="outline" size="sm" onClick={clearFilters}>
            Clear filters
          </Button>
        }
      />
    ) : (
      <EmptyState title="No users yet" description="Create the first account to get started." />
    );
  } else {
    body = (
      <>
        <UsersTable
          users={users.data.items}
          currentUserId={currentUser?.id ?? ""}
          busy={users.isFetching}
          onEdit={(user) => {
            setNotice(null);
            setEditing(user);
          }}
          onDelete={(user) => {
            setNotice(null);
            setDeleting(user);
          }}
        />
        <Pagination
          data={users.data}
          onPage={(page) => {
            updateParams({ ...params, page }, { push: true });
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
        title="Users"
        description="Accounts, roles, and weekly GPU-hour quotas."
        actions={
          <Button
            onClick={() => {
              setNotice(null);
              setCreating(true);
            }}
          >
            <Plus aria-hidden="true" />
            New user
          </Button>
        }
      />
      <Filters
        params={params}
        onChange={(changes) => {
          setNotice(null);
          updateParams(changes);
        }}
      />
      {/* Always rendered so screen readers announce the text when it appears. */}
      <p role="status" className={cn("text-sm text-muted-foreground", notice && "mb-4")}>
        {notice}
      </p>
      {body}

      <CreateUserDialog
        open={creating}
        onOpenChange={setCreating}
        onCreated={(user) => {
          setCreating(false);
          setNotice(`Created ${user.full_name}.`);
          void refreshList();
        }}
      />
      <EditUserDialog
        user={editing}
        isSelf={editing?.id === currentUser?.id}
        onOpenChange={(open) => {
          if (!open) setEditing(null);
        }}
        onSaved={(user) => {
          setEditing(null);
          setNotice(`Saved changes to ${user.full_name}.`);
          if (user.id === currentUser?.id) queryClient.setQueryData(meQueryKey, user);
          void refreshList();
        }}
      />
      <DeleteUserDialog
        user={deleting}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        onDeleted={(user) => {
          setDeleting(null);
          setNotice(`Deleted ${user.full_name}.`);
          void refreshList();
        }}
      />
    </>
  );
}
