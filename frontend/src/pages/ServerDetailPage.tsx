import { useQuery } from "@tanstack/react-query";
import { ChevronLeft } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router";

import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError } from "@/lib/api";
import type { Server } from "@/lib/api-types";
import { formatInLab } from "@/lib/lab-time";
import { cn } from "@/lib/utils";
import { isUuid } from "@/lib/validation";
import { AvailabilityTimeline } from "@/reservations/AvailabilityTimeline";
import { nextBookableStart } from "@/reservations/booking";
import { BookingDialog } from "@/reservations/BookingDialog";
import {
  AVAILABILITY_DAYS,
  availabilityKey,
  getAvailability,
} from "@/reservations/reservations-api";
import { GPU_MODELS, SERVER_STATUS_LABELS, getServer, serverKey } from "@/servers/servers-api";

const DAY_MS = 24 * 60 * 60_000;

const STATUS_VARIANTS = {
  available: "outline",
  maintenance: "secondary",
  retired: "destructive",
} as const;

function isNotFound(error: Error): boolean {
  return error instanceof ApiError && error.status === 404;
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

function unavailableReason(server: Server): string | null {
  if (!server.lab.is_active) return "This lab is closed, so its servers cannot be booked.";
  if (server.status === "maintenance") return "This server is in maintenance and cannot be booked.";
  if (server.status === "retired") return "This server is retired and cannot be booked.";
  return null;
}

function AvailabilityCard({
  server,
  onPickSlot,
}: {
  server: Server;
  onPickSlot: ((start: Date) => void) | undefined;
}) {
  // Fixed for the life of the page, so the query key stays stable.
  const [window] = useState(() => {
    const from = new Date();
    return { from, to: new Date(from.getTime() + AVAILABILITY_DAYS * DAY_MS) };
  });
  const availability = useQuery({
    queryKey: availabilityKey(server.id),
    queryFn: ({ signal }) => getAvailability(server.id, window.from, window.to, signal),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Availability</h2>
        </CardTitle>
        <CardDescription>
          Free GPUs per half hour for the next {AVAILABILITY_DAYS} days.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {availability.isPending ? (
          <LoadingState label="Loading availability" />
        ) : availability.isError ? (
          <ErrorState
            title="Could not load availability"
            message={availability.error.message}
            onRetry={() => {
              void availability.refetch();
            }}
          />
        ) : availability.data.runs.length === 0 ? (
          <EmptyState title="No availability" description="Nothing to show for this window." />
        ) : (
          <AvailabilityTimeline
            availability={availability.data}
            now={new Date()}
            onPickSlot={onPickSlot}
          />
        )}
      </CardContent>
    </Card>
  );
}

function ServerDetails({ server }: { server: Server }) {
  const gpu = GPU_MODELS[server.gpu_model].label;
  const [bookingStart, setBookingStart] = useState<Date | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const reason = unavailableReason(server);
  const bookable = reason === null;

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
            <Button
              size="sm"
              disabled={!bookable}
              onClick={() => {
                setNotice(null);
                setBookingStart(nextBookableStart(new Date()));
              }}
            >
              Book GPUs
            </Button>
          </div>
        }
      />
      <p
        role="status"
        className={cn("text-sm text-muted-foreground", (notice ?? reason) && "mb-4")}
      >
        {notice ?? reason}
      </p>
      <BookingDialog
        server={server}
        start={bookingStart}
        onOpenChange={(open) => {
          if (!open) setBookingStart(null);
        }}
        onBooked={(reservation) => {
          setBookingStart(null);
          const when = formatInLab(reservation.starts_at, server.lab.timezone);
          setNotice(
            reservation.status === "pending"
              ? `Requested ${String(reservation.gpu_count)} GPUs from ${when}. An admin will review it.`
              : `Booked ${String(reservation.gpu_count)} GPUs from ${when}.`,
          );
        }}
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
          <AvailabilityCard server={server} onPickSlot={bookable ? setBookingStart : undefined} />
        </div>
      </div>
    </>
  );
}

function NotFound() {
  return (
    <EmptyState
      title="Server not found"
      description="It may have been removed, or the link is wrong."
      action={
        <Link to="/servers" className="text-sm underline underline-offset-4">
          Browse servers
        </Link>
      }
    />
  );
}

export function ServerDetailPage() {
  const { serverId = "" } = useParams();
  // A malformed id cannot match a server; skip the request.
  const validId = isUuid(serverId);
  const server = useQuery({
    queryKey: serverKey(serverId),
    queryFn: ({ signal }) => getServer(serverId, signal),
    enabled: validId,
  });

  const header = <PageHeader title="Server details" description="Specs and availability." />;
  let body;
  if (!validId) {
    body = (
      <>
        {header}
        <NotFound />
      </>
    );
  } else if (server.isPending) {
    body = (
      <>
        {header}
        <LoadingState label="Loading server" />
      </>
    );
  } else if (server.isError) {
    body = (
      <>
        {header}
        {isNotFound(server.error) ? (
          <NotFound />
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
