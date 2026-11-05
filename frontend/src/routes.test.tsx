import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderRoute } from "@/test/render";

function stubFetch(response: () => Promise<Response>) {
  const fetchMock = vi.fn(response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("routing", () => {
  beforeEach(() => {
    stubFetch(() => Promise.resolve(Response.json({ status: "ok" })));
  });

  it.each([
    ["/servers", "Servers"],
    ["/servers/abc", "Server details"],
    ["/find-slot", "Find a slot"],
    ["/reservations", "My reservations"],
    ["/assistant", "Assistant"],
    ["/admin", "Admin"],
    ["/login", "Sign in"],
    ["/register", "Create an account"],
  ])("renders %s", async (path, heading) => {
    renderRoute(path);
    expect(
      await screen.findByText(heading, { selector: "h1, [data-slot='card-title']" }),
    ).toBeInTheDocument();
  });

  it("shows the main navigation on app pages", () => {
    renderRoute("/servers");
    expect(screen.getAllByRole("link", { name: "Find a slot" }).length).toBeGreaterThan(0);
  });

  it("renders a not-found page for unknown paths", async () => {
    renderRoute("/does-not-exist");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });
});

describe("dashboard API status", () => {
  it("shows loading, then online when the API responds", async () => {
    stubFetch(() => Promise.resolve(Response.json({ status: "ok" })));
    renderRoute("/");

    expect(screen.getByRole("status")).toHaveTextContent("Checking API status");
    expect(await screen.findByText("online")).toBeInTheDocument();
  });

  it("shows an error with a retry button when the API fails", async () => {
    stubFetch(() => Promise.resolve(new Response(null, { status: 503 })));
    renderRoute("/");

    expect(await screen.findByText("API unreachable")).toBeInTheDocument();
    expect(screen.getByText("Request failed with status 503")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
