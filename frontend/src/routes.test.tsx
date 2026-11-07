import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { fakeApi, signedInAs } from "@/test/fake-api";
import { renderRoute } from "@/test/render";

describe("routing", () => {
  it.each([
    ["/find-slot", "Find a slot"],
    ["/reservations", "My reservations"],
    ["/assistant", "Assistant"],
  ])("renders %s for a signed-in user", async (path, heading) => {
    fakeApi(...signedInAs("student"));
    renderRoute(path);
    expect(await screen.findByRole("heading", { name: heading })).toBeInTheDocument();
  });

  it("shows the main navigation on app pages", async () => {
    fakeApi(...signedInAs("student"));
    renderRoute("/find-slot");
    expect((await screen.findAllByRole("link", { name: "Find a slot" })).length).toBeGreaterThan(0);
  });

  it("renders a not-found page for unknown paths", async () => {
    fakeApi(...signedInAs("student"));
    renderRoute("/does-not-exist");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });
});
