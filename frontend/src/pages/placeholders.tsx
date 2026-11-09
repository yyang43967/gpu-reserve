import { Link } from "react-router";

import { PageHeader } from "@/components/page-header";

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
