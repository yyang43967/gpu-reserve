import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { safeNextPath } from "@/auth/next-path";
import { getAccessToken } from "@/lib/auth-token";
import {
  demoLogin,
  emptyDashboard,
  fakeApi,
  makeUser,
  on,
  problem,
  signedInAs,
  signedOut,
  tokenResponse,
} from "@/test/fake-api";
import { renderRoute } from "@/test/render";

function type(field: HTMLElement, value: string) {
  fireEvent.change(field, { target: { value } });
}

async function fillLogin(email: string, password: string) {
  type(await screen.findByLabelText("Email"), email);
  type(screen.getByLabelText("Password"), password);
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("session bootstrap", () => {
  it("restores the session from the refresh cookie on load", async () => {
    fakeApi(...signedInAs("student"), emptyDashboard);
    renderRoute("/");

    expect(await screen.findByText("Ada Lovelace")).toBeInTheDocument();
    expect(getAccessToken()).toBe("access-token");
  });

  it("sends signed-out visitors to the login page and remembers where they were", async () => {
    fakeApi(signedOut);
    const { router } = renderRoute("/servers?lab=vision");

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
    expect(router.state.location.search).toBe("?next=%2Fservers%3Flab%3Dvision");
  });

  it("does not add next=/ for the home page", async () => {
    fakeApi(signedOut);
    const { router } = renderRoute("/");

    await screen.findByRole("heading", { name: "Sign in" });
    expect(router.state.location.search).toBe("");
  });

  it("shows an error with retry when the server is unreachable", async () => {
    fakeApi(on("POST", "/auth/refresh", () => new Response(null, { status: 503 })));
    renderRoute("/");

    expect(await screen.findByText("Could not reach the server")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("sends signed-in users away from the login page", async () => {
    fakeApi(...signedInAs("student"));
    const { router } = renderRoute("/login?next=%2Freservations");

    await screen.findByRole("heading", { name: "My reservations" });
    expect(router.state.location.pathname).toBe("/reservations");
  });
});

describe("login", () => {
  it("signs in and continues to the remembered page", async () => {
    const requests = fakeApi(
      signedOut,
      on("POST", "/auth/login", () => Response.json(tokenResponse(makeUser(), "fresh"))),
    );
    const { router } = renderRoute("/login?next=%2Fassistant");

    await fillLogin("ada@example.edu", "correct-horse");

    await screen.findByRole("heading", { name: "Assistant" });
    expect(router.state.location.pathname).toBe("/assistant");
    expect(getAccessToken()).toBe("fresh");
    expect(requests.find((r) => r.path === "/auth/login")?.body).toEqual({
      email: "ada@example.edu",
      password: "correct-horse",
    });
  });

  it("validates before sending anything", async () => {
    const requests = fakeApi(signedOut);
    renderRoute("/login");

    fireEvent.click(await screen.findByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Enter a valid email address.")).toBeInTheDocument();
    expect(screen.getByText("Enter your password.")).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveAttribute("aria-invalid", "true");
    // Only the page's own reads: the session check and the demo account.
    expect(requests.map((r) => r.path).sort()).toEqual(["/auth/demo", "/auth/refresh"]);
  });

  it("shows wrong credentials as a form error", async () => {
    fakeApi(
      signedOut,
      on("POST", "/auth/login", () =>
        problem(401, "invalid-credentials", { detail: "Invalid email or password" }),
      ),
    );
    renderRoute("/login");

    await fillLogin("ada@example.edu", "wrong-password");

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
  });

  it("tells the user how long to wait when throttled", async () => {
    fakeApi(
      signedOut,
      on("POST", "/auth/login", () =>
        problem(429, "too-many-login-attempts", { headers: { "Retry-After": "540" } }),
      ),
    );
    renderRoute("/login");

    await fillLogin("ada@example.edu", "whatever-1");

    expect(await screen.findByRole("alert")).toHaveTextContent("Try again in 9 minutes.");
  });
});

describe("registration", () => {
  async function fillRegister(name: string, email: string, password: string) {
    type(await screen.findByLabelText("Full name"), name);
    type(screen.getByLabelText("Email"), email);
    type(screen.getByLabelText("Password"), password);
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
  }

  it("creates an account and signs in", async () => {
    fakeApi(
      signedOut,
      on("POST", "/auth/register", () => Response.json(tokenResponse(makeUser()), { status: 201 })),
    );
    const { router } = renderRoute("/register");

    await fillRegister("Ada Lovelace", "ada@example.edu", "long-enough-password");

    await screen.findByRole("heading", { name: "Dashboard" });
    expect(router.state.location.pathname).toBe("/");
  });

  it("checks the password length locally", async () => {
    fakeApi(signedOut);
    renderRoute("/register");

    await fillRegister("Ada", "ada@example.edu", "short");

    expect(await screen.findByText("Use at least 8 characters.")).toBeInTheDocument();
  });

  it("puts a taken email under the email field", async () => {
    fakeApi(
      signedOut,
      on("POST", "/auth/register", () =>
        problem(409, "email-taken", { detail: "An account with ada@example.edu already exists." }),
      ),
    );
    renderRoute("/register");

    await fillRegister("Ada", "ada@example.edu", "long-enough-password");

    const email = screen.getByLabelText("Email");
    await waitFor(() => {
      expect(email).toHaveAttribute("aria-invalid", "true");
    });
    expect(email).toHaveAccessibleDescription("An account with ada@example.edu already exists.");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("maps server validation errors to their fields", async () => {
    fakeApi(
      signedOut,
      on("POST", "/auth/register", () =>
        problem(422, "validation-failed", {
          errors: [{ field: "email", message: "The domain name example.invalid does not exist." }],
        }),
      ),
    );
    renderRoute("/register");

    await fillRegister("Ada", "ada@example.invalid", "long-enough-password");

    expect(
      await screen.findByText("The domain name example.invalid does not exist."),
    ).toBeInTheDocument();
  });
});

describe("roles", () => {
  it("hides the admin link from students and keeps them out of /admin", async () => {
    fakeApi(...signedInAs("student"));
    const { router } = renderRoute("/admin");

    await screen.findByRole("heading", { name: "Dashboard" });
    expect(router.state.location.pathname).toBe("/");
    for (const nav of screen.getAllByRole("navigation", { name: "Main" })) {
      expect(within(nav).queryByRole("link", { name: "Admin" })).toBeNull();
    }
  });

  it("shows the admin area to admins", async () => {
    fakeApi(...signedInAs("admin"));
    renderRoute("/admin");

    expect(await screen.findByRole("heading", { name: "Admin" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Admin" }).length).toBeGreaterThan(0);
  });
});

describe("sign out and expiry", () => {
  it("signs out and returns to the login page", async () => {
    const requests = fakeApi(...signedInAs("student"));
    const { router } = renderRoute("/servers");

    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));

    await screen.findByRole("heading", { name: "Sign in" });
    expect(router.state.location.pathname).toBe("/login");
    // A deliberate sign-out does not carry the page over to the next person's sign-in.
    expect(router.state.location.search).toBe("");
    expect(requests.some((r) => r.method === "POST" && r.path === "/auth/logout")).toBe(true);
    expect(getAccessToken()).toBeNull();
    // Only the refresh at page load: signing out must not try the cookie again.
    expect(requests.filter((r) => r.path === "/auth/refresh")).toHaveLength(1);
  });

  it("returns to login when the session can no longer be refreshed", async () => {
    let refreshes = 0;
    fakeApi(
      on("POST", "/auth/refresh", () => {
        refreshes += 1;
        // The first refresh (page load) works; the cookie is then revoked elsewhere.
        return refreshes === 1
          ? Response.json(tokenResponse(makeUser(), "short-lived"))
          : problem(401, "refresh-token-reused");
      }),
      on("GET", "/stats/overview", () => problem(401, "invalid-token")),
    );
    const { router } = renderRoute("/");

    await screen.findByRole("heading", { name: "Sign in" });
    expect(router.state.location.pathname).toBe("/login");
  });
});

describe("safeNextPath", () => {
  it.each([
    [null, "/"],
    ["", "/"],
    ["/servers?x=1", "/servers?x=1"],
    ["https://evil.example", "/"],
    ["//evil.example", "/"],
    ["/\\evil.example", "/"],
    ["javascript:alert(1)", "/"],
  ])("%s -> %s", (input, expected) => {
    expect(safeNextPath(input)).toBe(expected);
  });
});

describe("demo account", () => {
  it("is offered on the login page and signs in with one click", async () => {
    const requests = fakeApi(
      demoLogin("ada@example.edu", "demo-password"),
      signedOut,
      on("POST", "/auth/login", () => Response.json(tokenResponse(makeUser()))),
      emptyDashboard,
    );
    renderRoute("/login");

    const demo = await screen.findByRole("region", { name: "Try the demo" });
    expect(within(demo).getByText("ada@example.edu")).toBeInTheDocument();
    expect(within(demo).getByText("demo-password")).toBeInTheDocument();
    fireEvent.click(within(demo).getByRole("button", { name: "Sign in as the demo student" }));

    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(requests.find((r) => r.path === "/auth/login")?.body).toEqual({
      email: "ada@example.edu",
      password: "demo-password",
    });
  });

  it("is not shown when the deployment has none", async () => {
    fakeApi(signedOut);
    renderRoute("/login");

    await screen.findByRole("button", { name: "Sign in" });
    await waitFor(() => {
      expect(screen.queryByRole("region", { name: "Try the demo" })).not.toBeInTheDocument();
    });
  });
});
