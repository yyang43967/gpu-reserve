import { Link, useParams } from "react-router";

import { EmptyState } from "@/components/states";

function PageHeader({ title, description }: { title: string; description: string }) {
  return (
    <div className="mb-6">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      <p className="mt-1 text-sm text-muted-foreground">{description}</p>
    </div>
  );
}

function ComingSoon({ title, description }: { title: string; description: string }) {
  return (
    <>
      <PageHeader title={title} description={description} />
      <EmptyState title="Nothing here yet" description="This page is under construction." />
    </>
  );
}

export function ServersPage() {
  return <ComingSoon title="Servers" description="Browse GPU servers across labs." />;
}

export function ServerDetailPage() {
  const { serverId } = useParams();
  return (
    <ComingSoon
      title="Server details"
      description={`Specs and availability for server ${serverId ?? ""}.`}
    />
  );
}

export function FindSlotPage() {
  return (
    <ComingSoon title="Find a slot" description="Search for GPUs that fit your job and window." />
  );
}

export function MyReservationsPage() {
  return <ComingSoon title="My reservations" description="Upcoming and past reservations." />;
}

export function AdminPage() {
  return <ComingSoon title="Admin" description="Labs, servers, users, and approvals." />;
}

export function AssistantPage() {
  return (
    <ComingSoon title="Assistant" description="Ask about policies and find hardware for a job." />
  );
}

export function NotFoundPage() {
  return (
    <>
      <PageHeader title="Page not found" description="The page you requested does not exist." />
      <Link to="/" className="text-sm underline underline-offset-4">
        Back to the dashboard
      </Link>
    </>
  );
}
