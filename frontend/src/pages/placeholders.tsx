import { Link } from "react-router";

import { PageHeader } from "@/components/page-header";
import { EmptyState } from "@/components/states";

function ComingSoon({ title, description }: { title: string; description: string }) {
  return (
    <>
      <PageHeader title={title} description={description} />
      <EmptyState title="Nothing here yet" description="This page is under construction." />
    </>
  );
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
