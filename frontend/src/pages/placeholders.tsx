import type { ReactNode } from "react";
import { Link, useParams } from "react-router";

import { EmptyState } from "@/components/states";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

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

function AuthCard({ title, footer }: { title: string; footer: ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>Sign-in is not available yet.</CardDescription>
      </CardHeader>
      <CardContent className="text-sm">{footer}</CardContent>
    </Card>
  );
}

export function LoginPage() {
  return (
    <AuthCard
      title="Sign in"
      footer={
        <p>
          No account?{" "}
          <Link to="/register" className="underline underline-offset-4">
            Register
          </Link>
        </p>
      }
    />
  );
}

export function RegisterPage() {
  return (
    <AuthCard
      title="Create an account"
      footer={
        <p>
          Already registered?{" "}
          <Link to="/login" className="underline underline-offset-4">
            Sign in
          </Link>
        </p>
      }
    />
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
