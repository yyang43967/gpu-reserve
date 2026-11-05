import type { RouteObject } from "react-router";

import { AppLayout, AuthLayout } from "@/components/layout/AppLayout";
import { DashboardPage } from "@/pages/DashboardPage";
import {
  AdminPage,
  AssistantPage,
  FindSlotPage,
  LoginPage,
  MyReservationsPage,
  NotFoundPage,
  RegisterPage,
  ServerDetailPage,
  ServersPage,
} from "@/pages/placeholders";
import { RouteErrorPage } from "@/pages/RouteErrorPage";

export const routes: RouteObject[] = [
  {
    element: <AuthLayout />,
    errorElement: <RouteErrorPage />,
    children: [
      { path: "/login", element: <LoginPage /> },
      { path: "/register", element: <RegisterPage /> },
    ],
  },
  {
    element: <AppLayout />,
    errorElement: <RouteErrorPage />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "servers", element: <ServersPage /> },
      { path: "servers/:serverId", element: <ServerDetailPage /> },
      { path: "find-slot", element: <FindSlotPage /> },
      { path: "reservations", element: <MyReservationsPage /> },
      { path: "assistant", element: <AssistantPage /> },
      { path: "admin", element: <AdminPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];
