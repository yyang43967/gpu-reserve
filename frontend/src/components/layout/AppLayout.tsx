import { LogOut, Menu, X } from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router";

import { useAuth } from "@/auth/auth-context";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { to: "/", label: "Dashboard", end: true, adminOnly: false },
  { to: "/servers", label: "Servers", end: false, adminOnly: false },
  { to: "/find-slot", label: "Find a slot", end: false, adminOnly: false },
  { to: "/reservations", label: "My reservations", end: false, adminOnly: false },
  { to: "/assistant", label: "Assistant", end: false, adminOnly: false },
  { to: "/admin", label: "Admin", end: false, adminOnly: true },
] as const;

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const { user } = useAuth();
  const items = NAV_ITEMS.filter((item) => !item.adminOnly || user?.role === "admin");
  return (
    <>
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              "rounded-md px-3 py-2 text-sm font-medium transition-colors",
              isActive
                ? "bg-secondary text-secondary-foreground"
                : "text-muted-foreground hover:text-foreground",
            )
          }
        >
          {item.label}
        </NavLink>
      ))}
    </>
  );
}

function UserMenu() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [signingOut, setSigningOut] = useState(false);

  const signOut = async () => {
    setSigningOut(true);
    try {
      await logout();
    } finally {
      void navigate("/login", { replace: true });
    }
  };

  return (
    <div className="flex items-center gap-2">
      <span className="hidden max-w-40 truncate text-sm text-muted-foreground sm:inline">
        {user?.full_name}
      </span>
      <Button
        variant="ghost"
        size="sm"
        disabled={signingOut}
        onClick={() => {
          void signOut();
        }}
      >
        <LogOut aria-hidden="true" />
        Sign out
      </Button>
    </div>
  );
}

export function AppLayout() {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className="min-h-dvh">
      <header className="border-b">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-4">
          <NavLink to="/" className="font-semibold">
            GPU Reserve
          </NavLink>
          <nav aria-label="Main" className="hidden items-center gap-1 md:flex">
            <NavLinks />
          </nav>
          <div className="flex items-center gap-1">
            <UserMenu />
            <Button
              variant="ghost"
              size="icon"
              className="md:hidden"
              aria-label={menuOpen ? "Close menu" : "Open menu"}
              aria-expanded={menuOpen}
              aria-controls="mobile-nav"
              onClick={() => {
                setMenuOpen((open) => !open);
              }}
            >
              {menuOpen ? <X aria-hidden="true" /> : <Menu aria-hidden="true" />}
            </Button>
          </div>
        </div>
        {menuOpen && (
          <nav
            id="mobile-nav"
            aria-label="Main"
            className="flex flex-col gap-1 border-t px-4 py-2 md:hidden"
          >
            <NavLinks
              onNavigate={() => {
                setMenuOpen(false);
              }}
            />
          </nav>
        )}
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}

export function AuthLayout() {
  return (
    <main className="flex min-h-dvh items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <Outlet />
      </div>
    </main>
  );
}
