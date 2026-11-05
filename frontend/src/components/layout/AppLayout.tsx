import { Menu, X } from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet } from "react-router";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/servers", label: "Servers", end: false },
  { to: "/find-slot", label: "Find a slot", end: false },
  { to: "/reservations", label: "My reservations", end: false },
  { to: "/assistant", label: "Assistant", end: false },
  { to: "/admin", label: "Admin", end: false },
] as const;

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <>
      {NAV_ITEMS.map((item) => (
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
