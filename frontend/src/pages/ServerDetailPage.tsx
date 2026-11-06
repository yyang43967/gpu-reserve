import { useQuery } from "@tanstack/react-query";
import { CalendarClock, ChevronLeft } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router";

import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError } from "@/lib/api";
import type { Server } from "@/lib/api-types";
import { GPU_MODELS, SERVER_STATUS_LABELS, getServer, serverKey } from "@/servers/servers-api";

const STATUS_VARIANTS = {
  available: "outline",
  maintenance: "secondary",
  retired: "destructive",
} as const;

/** A missing server, or an id that is not even well formed. */
function isNotFound(error: Error): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 422);
}

function BackLink() {
  return (
    <Link
      to="/servers"
      className="mb-2 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
    >
      <ChevronLeft className="size-4" aria-hidden="true" />
      Servers
    </Link>
  );
}

function Spec({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 border-b py-2 last:border-b-0 sm:flex-row sm:justify-between sm:gap-4">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-sm font-medium sm:text-right">{children}</dd>
    </div>
  );
}

function ServerDetails({ server }: { server: Server }) {
  const gpu = GPU_MODELS[server.gpu_model].label;

  return (
    <>
      <PageHeader
        title={server.hostname}
        description={`${server.lab.name} · ${server.lab.timezone}`}
        actions={
          <div className="flex flex-wrap gap-2">
            <Badge variant={STATUS_VARIANTS[server.status]}>
              {SERVER_STATUS_LABELS[server.status]}
            </Badge>
            {server.requires_approval && <Badge variant="secondary">Requires approval</Badge>}
            {!server.lab.is_active && <Badge variant="destructive">Lab inactive</Badge>}
          </div>
        }
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        {/* First on phones, where the specs matter more than the picture. */}
        <div className="flex flex-col gap-6 lg:order-last">
          <Card>
            <CardHeader>
              <CardTitle>
                <h2>Specifications</h2>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <dl>
                <Spec label="GPUs">
                  {server.gpu_count} × {gpu}
                </Spec>
                <Spec label="Memory per GPU">{server.gpu_memory_gb} GB</Spec>
                <Spec label="Total GPU memory">{server.gpu_memory_gb * server.gpu_count} GB</Spec>
                <Spec label="CPU cores">{server.cpu_cores}</Spec>
                <Spec label="RAM">{server.ram_gb} GB</Spec>
                <Spec label="Interconnect">{server.interconnect ?? "Not specified"}</Spec>
              </dl>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>
                <h2>Booking</h2>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <dl>
                <Spec label="Longest booking">
                  {server.max_booking_hours} {server.max_booking_hours === 1 ? "hour" : "hours"}
                </Spec>
                <Spec label="Approval">
                  {server.requires_approval ? "An admin approves each booking" : "Not required"}
                </Spec>
                <Spec label="Lab">{server.lab.name}</Spec>
                <Spec label="Lab time zone">{server.lab.timezone}</Spec>
              </dl>
            </CardContent>
          </Card>
          {server.notes && (
            <Card>
              <CardHeader>
                <CardTitle>
                  <h2>Notes</h2>
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm [overflow-wrap:anywhere] whitespace-pre-line">
                  {server.notes}
                </p>
              </CardContent>
            </Card>
          )}
        </div>
        <div className="flex flex-col gap-6">
          {server.image_url && (
            <img
              src={server.image_url}
              alt={`The ${server.hostname} server`}
              className="aspect-video w-full rounded-lg border object-cover"
            />
          )}
          <Card>
            <CardHeader>
              <CardTitle>
                <h2>Availability</h2>
              </CardTitle>
              <CardDescription>Booked and free GPUs over the coming days.</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed p-8 text-center">
                <CalendarClock className="size-8 text-muted-foreground" aria-hidden="true" />
                <p className="font-medium">Availability timeline coming soon</p>
                <p className="text-sm text-muted-foreground">
                  Reservations are not open yet. This is where free and booked GPUs will show.
                </p>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </>
  );
}

export function ServerDetailPage() {
  const { serverId = "" } = useParams();
  const server = useQuery({
    queryKey: serverKey(serverId),
    queryFn: ({ signal }) => getServer(serverId, signal),
  });

  let body;
  if (server.isPending) {
    body = (
      <>
        <PageHeader title="Server details" description="Specs and availability." />
        <LoadingState label="Loading server" />
      </>
    );
  } else if (server.isError) {
    body = (
      <>
        <PageHeader title="Server details" description="Specs and availability." />
        {isNotFound(server.error) ? (
          <EmptyState
            title="Server not found"
            description="It may have been removed, or the link is wrong."
            action={
              <Link to="/servers" className="text-sm underline underline-offset-4">
                Browse servers
              </Link>
            }
          />
        ) : (
          <ErrorState
            title="Could not load this server"
            message={server.error.message}
            onRetry={() => {
              void server.refetch();
            }}
          />
        )}
      </>
    );
  } else {
    body = <ServerDetails server={server.data} />;
  }

  return (
    <>
      <BackLink />
      {body}
    </>
  );
}
