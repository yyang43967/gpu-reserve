import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { fakeApi, healthy, on, signedInAs } from "@/test/fake-api";
import { renderRoute } from "@/test/render";

describe("routing", () => {
  it.each([
    ["/find-slot", "Find a slot"],
    ["/reservations", "My reservations"],
    ["/assistant", "Assistant"],
  ])("renders %s for a signed-in user", async (path, heading) => {
    fakeApi(...signedInAs("student"), healthy);
    renderRoute(path);
    expect(await screen.findByRole("heading", { name: heading })).toBeInTheDocument();
  });

  it("shows the main navigation on app pages", async () => {
    fakeApi(...signedInAs("student"), healthy);
    renderRoute("/find-slot");
    expect((await screen.findAllByRole("link", { name: "Find a slot" })).length).toBeGreaterThan(0);
  });

  it("renders a not-found page for unknown paths", async () => {
    fakeApi(...signedInAs("student"), healthy);
    renderRoute("/does-not-exist");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });
});

describe("dashboard API status", () => {
  it("shows loading, then online when the API responds", async () => {
    fakeApi(...signedInAs("student"), healthy);
    renderRoute("/");

    expect(await screen.findByText("Checking API status")).toBeInTheDocument();
    expect(await screen.findByText("online")).toBeInTheDocument();
  });

  it("shows an error with a retry button when the API fails", async () => {
    fakeApi(
      ...signedInAs("student"),
      on("GET", "/health", () => new Response(null, { status: 503 })),
    );
    renderRoute("/");

    expect(await screen.findByText("API unreachable")).toBeInTheDocument();
    expect(screen.getByText("Request failed with status 503")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
