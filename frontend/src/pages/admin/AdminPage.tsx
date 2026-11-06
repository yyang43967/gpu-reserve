import { Building2, ChevronRight, Server, Users } from "lucide-react";
import { Link } from "react-router";

import { PageHeader } from "@/components/page-header";

const SECTIONS = [
  {
    to: "/admin/users",
    title: "Users",
    description: "Create accounts, change roles and quotas, deactivate or delete users.",
    icon: Users,
  },
  {
    to: "/admin/labs",
    title: "Labs",
    description: "Add labs, set their time zones, and deactivate labs that close.",
    icon: Building2,
  },
  {
    to: "/admin/servers",
    title: "Servers",
    description: "Add servers, change GPU counts and booking rules, and upload photos.",
    icon: Server,
  },
] as const;

export function AdminPage() {
  return (
    <>
      <PageHeader title="Admin" description="Manage the people and hardware in the system." />
      <ul className="grid gap-4 sm:grid-cols-2">
        {SECTIONS.map(({ to, title, description, icon: Icon }) => (
          <li key={to}>
            <Link
              to={to}
              className="flex items-center gap-4 rounded-xl border p-4 shadow-sm transition-colors outline-none hover:bg-muted/50 focus-visible:ring-[3px] focus-visible:ring-ring/50"
            >
              <Icon className="size-6 shrink-0 text-muted-foreground" aria-hidden="true" />
              <span className="grid flex-1 gap-1">
                <span className="font-medium">{title}</span>
                <span className="text-sm text-muted-foreground">{description}</span>
              </span>
              <ChevronRight className="size-4 text-muted-foreground" aria-hidden="true" />
            </Link>
          </li>
        ))}
      </ul>
    </>
  );
}
