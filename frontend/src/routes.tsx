import type { RouteObject } from "react-router";

import { RedirectIfAuthenticated, RequireAdmin, RequireAuth } from "@/auth/guards";
import { AppLayout, AuthLayout } from "@/components/layout/AppLayout";
import { AccountPage } from "@/pages/AccountPage";
import { AdminApprovalsPage } from "@/pages/admin/AdminApprovalsPage";
import { AdminLabsPage } from "@/pages/admin/AdminLabsPage";
import { AdminPage } from "@/pages/admin/AdminPage";
import { AdminServersPage } from "@/pages/admin/AdminServersPage";
import { AdminUsersPage } from "@/pages/admin/AdminUsersPage";
import { AdminUtilizationPage } from "@/pages/admin/AdminUtilizationPage";
import { DashboardPage } from "@/pages/DashboardPage";
import { LoginPage } from "@/pages/LoginPage";
import { MyReservationsPage } from "@/pages/MyReservationsPage";
import { FindSlotPage } from "@/pages/FindSlotPage";
import { AssistantPage } from "@/pages/AssistantPage";
import { NotFoundPage } from "@/pages/placeholders";
import { RegisterPage } from "@/pages/RegisterPage";
import { RouteErrorPage } from "@/pages/RouteErrorPage";
import { ServerDetailPage } from "@/pages/ServerDetailPage";
import { ServersPage } from "@/pages/ServersPage";

export const routes: RouteObject[] = [
  {
    element: <RedirectIfAuthenticated />,
    errorElement: <RouteErrorPage />,
    children: [
      {
        element: <AuthLayout />,
        children: [
          { path: "/login", element: <LoginPage /> },
          { path: "/register", element: <RegisterPage /> },
        ],
      },
    ],
  },
  {
    element: <RequireAuth />,
    errorElement: <RouteErrorPage />,
    children: [
      {
        element: <AppLayout />,
        children: [
          { index: true, element: <DashboardPage /> },
          { path: "servers", element: <ServersPage /> },
          { path: "servers/:serverId", element: <ServerDetailPage /> },
          { path: "find-slot", element: <FindSlotPage /> },
          { path: "reservations", element: <MyReservationsPage /> },
          { path: "assistant", element: <AssistantPage /> },
          { path: "account", element: <AccountPage /> },
          {
            element: <RequireAdmin />,
            children: [
              { path: "admin", element: <AdminPage /> },
              { path: "admin/users", element: <AdminUsersPage /> },
              { path: "admin/labs", element: <AdminLabsPage /> },
              { path: "admin/servers", element: <AdminServersPage /> },
              { path: "admin/approvals", element: <AdminApprovalsPage /> },
              { path: "admin/utilization", element: <AdminUtilizationPage /> },
            ],
          },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
];
