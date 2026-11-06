import type { RouteObject } from "react-router";

import { RedirectIfAuthenticated, RequireAdmin, RequireAuth } from "@/auth/guards";
import { AppLayout, AuthLayout } from "@/components/layout/AppLayout";
import { AccountPage } from "@/pages/AccountPage";
import { AdminPage } from "@/pages/admin/AdminPage";
import { AdminUsersPage } from "@/pages/admin/AdminUsersPage";
import { DashboardPage } from "@/pages/DashboardPage";
import { LoginPage } from "@/pages/LoginPage";
import {
  AssistantPage,
  FindSlotPage,
  MyReservationsPage,
  NotFoundPage,
  ServerDetailPage,
  ServersPage,
} from "@/pages/placeholders";
import { RegisterPage } from "@/pages/RegisterPage";
import { RouteErrorPage } from "@/pages/RouteErrorPage";

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
            ],
          },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
];
