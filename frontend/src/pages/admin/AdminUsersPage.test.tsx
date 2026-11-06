import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { User } from "@/lib/api-types";
import {
  fakeApi,
  healthy,
  makeUser,
  on,
  page,
  problem,
  signedInAs,
  type FakeRequest,
} from "@/test/fake-api";
import { renderRoute } from "@/test/render";

// signedInAs("admin") signs in as makeUser(), whose id is "user-1".
const me = makeUser({ id: "user-1", role: "admin", full_name: "Ada Lovelace" });
const grace = makeUser({ id: "user-2", email: "grace@example.edu", full_name: "Grace Hopper" });
const alan = makeUser({
  id: "user-3",
  email: "alan@example.edu",
  full_name: "Alan Turing",
  is_active: false,
  weekly_gpu_hour_quota: 12,
});

/**
 * A stateful /users API: filters and pages like the server, and remembers creates,
 * updates and deletes so the refetched list reflects them.
 */
function usersApi(initial: User[], pageSize = 20) {
  const users = [...initial];
  return (request: FakeRequest): Response | undefined => {
    if (request.method === "GET" && request.path === "/users") {
      const q = request.query.get("q")?.toLowerCase() ?? "";
      const role = request.query.get("role");
      const active = request.query.get("is_active");
      const matching = users.filter(
        (user) =>
          (!q || `${user.email} ${user.full_name}`.toLowerCase().includes(q)) &&
          (!role || user.role === role) &&
          (!active || String(user.is_active) === active),
      );
      const number = Number(request.query.get("page") ?? "1");
      const start = (number - 1) * pageSize;
      return Response.json(
        page(matching.slice(start, start + pageSize), {
          total: matching.length,
          page: number,
          page_size: pageSize,
        }),
      );
    }
    if (request.method === "POST" && request.path === "/users") {
      const body = request.body as Partial<User>;
      const created = makeUser({ ...body, id: `user-${String(users.length + 10)}` });
      users.unshift(created);
      return Response.json(created, { status: 201 });
    }
    const match = /^\/users\/([^/]+)$/.exec(request.path);
    const index = users.findIndex((user) => user.id === match?.[1]);
    if (!match || index === -1) return undefined;
    if (request.method === "PATCH") {
      const updated = { ...users[index], ...(request.body as Partial<User>) } as User;
      users[index] = updated;
      return Response.json(updated);
    }
    if (request.method === "DELETE") {
      users.splice(index, 1);
      return new Response(null, { status: 204 });
    }
    return undefined;
  };
}

function type(field: HTMLElement, value: string) {
  fireEvent.change(field, { target: { value } });
}

function userRequests(requests: FakeRequest[], method = "GET") {
  return requests.filter((r) => r.method === method && r.path.startsWith("/users"));
}

function lastListQuery(requests: FakeRequest[]) {
  return Object.fromEntries(userRequests(requests).at(-1)?.query ?? []);
}

async function findRow(name: string) {
  const cell = await screen.findByText(name);
  const row = cell.closest("tr");
  if (!row) throw new Error(`No table row for ${name}`);
  return row;
}

describe("admin hub", () => {
  it("links to user management", async () => {
    fakeApi(...signedInAs("admin"), usersApi([me]));
    const { router } = renderRoute("/admin");

    fireEvent.click(await screen.findByRole("link", { name: /Users/ }));

    expect(await screen.findByRole("heading", { name: "Users" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/admin/users");
  });

  it("keeps students out of user management", async () => {
    const requests = fakeApi(...signedInAs("student"), healthy);
    const { router } = renderRoute("/admin/users");

    await screen.findByRole("heading", { name: "Dashboard" });
    expect(router.state.location.pathname).toBe("/");
    expect(userRequests(requests)).toHaveLength(0);
  });
});

describe("user list", () => {
  it("shows a loading state, then the users", async () => {
    fakeApi(...signedInAs("admin"), usersApi([me, grace, alan]));
    renderRoute("/admin/users");

    expect(await screen.findByText("Loading users")).toBeInTheDocument();

    const graceRow = await findRow("Grace Hopper");
    expect(within(graceRow).getByText("grace@example.edu")).toBeInTheDocument();
    expect(within(graceRow).getByText("Student")).toBeInTheDocument();
    expect(within(graceRow).getByText("Active")).toBeInTheDocument();

    const alanRow = await findRow("Alan Turing");
    expect(within(alanRow).getByText("Inactive")).toBeInTheDocument();
    expect(within(alanRow).getByText("12")).toBeInTheDocument();
  });

  it("marks the signed-in admin and offers no way to delete them", async () => {
    fakeApi(...signedInAs("admin"), usersApi([me, grace]));
    renderRoute("/admin/users");

    const myRow = (await screen.findByText("(you)")).closest("tr");
    expect(myRow).not.toBeNull();
    if (!myRow) return;
    expect(within(myRow).getByText("Admin")).toBeInTheDocument();
    expect(within(myRow).getByRole("button", { name: "Edit Ada Lovelace" })).toBeInTheDocument();
    expect(within(myRow).queryByRole("button", { name: /Delete/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Delete Grace Hopper" })).toBeInTheDocument();
  });

  it("shows an error with retry", async () => {
    let calls = 0;
    const api = usersApi([me, grace]);
    fakeApi(...signedInAs("admin"), (request) => {
      if (request.method === "GET" && request.path === "/users" && ++calls === 1) {
        return new Response(null, { status: 503 });
      }
      return api(request);
    });
    renderRoute("/admin/users");

    expect(await screen.findByText("Could not load users")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("Grace Hopper")).toBeInTheDocument();
  });

  it("searches by name or email and keeps the search in the URL", async () => {
    const requests = fakeApi(...signedInAs("admin"), usersApi([me, grace, alan]));
    const { router } = renderRoute("/admin/users?page=2");

    await screen.findByRole("search");
    type(screen.getByLabelText("Search"), "  grace ");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => {
      expect(screen.queryByText("Alan Turing")).toBeNull();
    });
    expect(screen.getByText("Grace Hopper")).toBeInTheDocument();
    expect(router.state.location.search).toBe("?q=grace");
    expect(lastListQuery(requests)).toMatchObject({ q: "grace", page: "1" });
  });

  it("filters by role and status", async () => {
    const requests = fakeApi(...signedInAs("admin"), usersApi([me, grace, alan]));
    const { router } = renderRoute("/admin/users");

    await findRow("Grace Hopper");
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "inactive" } });

    await waitFor(() => {
      expect(screen.queryByText("Grace Hopper")).toBeNull();
    });
    expect(screen.getByText("Alan Turing")).toBeInTheDocument();
    expect(lastListQuery(requests)).toMatchObject({ is_active: "false" });

    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "admin" } });

    expect(await screen.findByText("No users match these filters")).toBeInTheDocument();
    expect(lastListQuery(requests)).toMatchObject({ role: "admin", is_active: "false" });
    expect(router.state.location.search).toBe("?role=admin&status=inactive");
  });

  it("clears filters from the empty state", async () => {
    fakeApi(...signedInAs("admin"), usersApi([me, grace]));
    const { router } = renderRoute("/admin/users?q=nobody");

    expect(await screen.findByText("No users match these filters")).toBeInTheDocument();
    expect(screen.getByLabelText("Search")).toHaveValue("nobody");

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));

    expect(await screen.findByText("Grace Hopper")).toBeInTheDocument();
    expect(router.state.location.search).toBe("");
    expect(screen.getByLabelText("Search")).toHaveValue("");
  });

  it("ignores unknown filter values in the URL", async () => {
    const requests = fakeApi(...signedInAs("admin"), usersApi([me, grace]));
    renderRoute("/admin/users?role=root&status=maybe&page=-3");

    await findRow("Grace Hopper");
    expect(lastListQuery(requests)).toEqual({ page: "1", page_size: "20" });
  });

  it("pages through the results", async () => {
    const many = Array.from({ length: 5 }, (_, i) =>
      makeUser({ id: `bulk-${String(i)}`, full_name: `Student ${String(i)}` }),
    );
    const requests = fakeApi(...signedInAs("admin"), usersApi(many, 2));
    const { router } = renderRoute("/admin/users");

    expect(await screen.findByText("1–2 of 5")).toBeInTheDocument();
    expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("3–4 of 5")).toBeInTheDocument();
    expect(lastListQuery(requests)).toMatchObject({ page: "2" });
    expect(router.state.location.search).toBe("?page=2");

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("5–5 of 5")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("moves back to the last page when the requested page no longer exists", async () => {
    fakeApi(...signedInAs("admin"), usersApi([me, grace]));
    const { router } = renderRoute("/admin/users?page=4");

    expect(await screen.findByText("Grace Hopper")).toBeInTheDocument();
    expect(router.state.location.search).toBe("");
  });
});

describe("creating a user", () => {
  async function openCreate() {
    fireEvent.click(await screen.findByRole("button", { name: "New user" }));
    return screen.findByRole("dialog", { name: "Create user" });
  }

  it("validates before sending anything", async () => {
    const requests = fakeApi(...signedInAs("admin"), usersApi([me]));
    renderRoute("/admin/users");
    const dialog = await openCreate();

    type(within(dialog).getByLabelText("Weekly quota"), "-1");
    fireEvent.click(within(dialog).getByRole("button", { name: "Create user" }));

    expect(await within(dialog).findByText("Enter a name.")).toBeInTheDocument();
    expect(within(dialog).getByText("Enter a valid email address.")).toBeInTheDocument();
    expect(within(dialog).getByText("Use at least 8 characters.")).toBeInTheDocument();
    expect(within(dialog).getByText("The quota cannot be negative.")).toBeInTheDocument();
    expect(userRequests(requests, "POST")).toHaveLength(0);
  });

  it("creates the user and shows it in the list", async () => {
    const requests = fakeApi(...signedInAs("admin"), usersApi([me]));
    renderRoute("/admin/users");
    const dialog = await openCreate();

    type(within(dialog).getByLabelText("Full name"), "Katherine Johnson");
    type(within(dialog).getByLabelText("Email"), "katherine@example.edu");
    type(within(dialog).getByLabelText("Initial password"), "orbital-mechanics");
    type(within(dialog).getByLabelText("Role"), "admin");
    type(within(dialog).getByLabelText("Weekly quota"), "100");
    fireEvent.click(within(dialog).getByRole("button", { name: "Create user" }));

    expect(await screen.findByText("Created Katherine Johnson.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(await findRow("Katherine Johnson")).toBeInTheDocument();
    expect(userRequests(requests, "POST")[0]?.body).toEqual({
      full_name: "Katherine Johnson",
      email: "katherine@example.edu",
      password: "orbital-mechanics",
      role: "admin",
      weekly_gpu_hour_quota: 100,
    });
  });

  it("puts a taken email under the email field", async () => {
    fakeApi(
      ...signedInAs("admin"),
      on("POST", "/users", () =>
        problem(409, "email-taken", {
          detail: "An account with grace@example.edu already exists.",
        }),
      ),
      usersApi([me]),
    );
    renderRoute("/admin/users");
    const dialog = await openCreate();

    type(within(dialog).getByLabelText("Full name"), "Grace");
    type(within(dialog).getByLabelText("Email"), "grace@example.edu");
    type(within(dialog).getByLabelText("Initial password"), "long-enough-password");
    fireEvent.click(within(dialog).getByRole("button", { name: "Create user" }));

    const email = within(dialog).getByLabelText("Email");
    await waitFor(() => {
      expect(email).toHaveAttribute("aria-invalid", "true");
    });
    expect(email).toHaveAccessibleDescription("An account with grace@example.edu already exists.");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("starts with an empty form each time it opens", async () => {
    fakeApi(...signedInAs("admin"), usersApi([me]));
    renderRoute("/admin/users");

    let dialog = await openCreate();
    type(within(dialog).getByLabelText("Full name"), "Half typed");
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });

    dialog = await openCreate();
    expect(within(dialog).getByLabelText("Full name")).toHaveValue("");
  });
});

describe("editing a user", () => {
  async function openEdit(name: string) {
    fireEvent.click(await screen.findByRole("button", { name: `Edit ${name}` }));
    return screen.findByRole("dialog", { name: `Edit ${name}` });
  }

  it("sends only the fields that changed", async () => {
    const requests = fakeApi(...signedInAs("admin"), usersApi([me, grace]));
    renderRoute("/admin/users");
    const dialog = await openEdit("Grace Hopper");

    const save = within(dialog).getByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled();

    type(within(dialog).getByLabelText("Weekly quota"), "96");
    fireEvent.click(save);

    expect(await screen.findByText("Saved changes to Grace Hopper.")).toBeInTheDocument();
    expect(userRequests(requests, "PATCH")).toEqual([
      expect.objectContaining({ path: "/users/user-2", body: { weekly_gpu_hour_quota: 96 } }),
    ]);
    expect(within(await findRow("Grace Hopper")).getByText("96")).toBeInTheDocument();
  });

  it("deactivates a user with the switch", async () => {
    const requests = fakeApi(...signedInAs("admin"), usersApi([me, grace]));
    renderRoute("/admin/users");
    const dialog = await openEdit("Grace Hopper");

    const active = within(dialog).getByRole("switch", { name: "Active" });
    expect(active).toBeChecked();
    fireEvent.click(active);
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await screen.findByText("Saved changes to Grace Hopper.");
    expect(userRequests(requests, "PATCH")[0]?.body).toEqual({ is_active: false });
    await waitFor(async () => {
      expect(within(await findRow("Grace Hopper")).getByText("Inactive")).toBeInTheDocument();
    });
  });

  it("does not let admins demote or deactivate themselves", async () => {
    fakeApi(...signedInAs("admin"), usersApi([me, grace]));
    renderRoute("/admin/users");
    const dialog = await openEdit("Ada Lovelace");

    expect(within(dialog).getByLabelText("Role")).toBeDisabled();
    expect(within(dialog).getByRole("switch", { name: "Active" })).toBeDisabled();
    expect(within(dialog).getByLabelText("Weekly quota")).toBeEnabled();
    expect(within(dialog).getByText(/cannot change your own role/)).toBeInTheDocument();
  });

  it("shows a refusal from the server in the dialog", async () => {
    fakeApi(
      ...signedInAs("admin"),
      (request) =>
        request.method === "PATCH"
          ? problem(409, "cannot-modify-self", {
              detail: "You cannot demote or deactivate your own account.",
            })
          : undefined,
      usersApi([me, grace]),
    );
    renderRoute("/admin/users");
    const dialog = await openEdit("Grace Hopper");

    type(within(dialog).getByLabelText("Role"), "admin");
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "You cannot demote or deactivate your own account.",
    );
  });
});

describe("deleting a user", () => {
  it("asks for confirmation, then deletes", async () => {
    const requests = fakeApi(...signedInAs("admin"), usersApi([me, grace]));
    renderRoute("/admin/users");

    fireEvent.click(await screen.findByRole("button", { name: "Delete Grace Hopper" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete Grace Hopper?" });
    expect(dialog).toHaveTextContent("grace@example.edu");

    fireEvent.click(within(dialog).getByRole("button", { name: "Delete user" }));

    expect(await screen.findByText("Deleted Grace Hopper.")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole("cell", { name: /Grace Hopper/ })).toBeNull();
    });
    expect(userRequests(requests, "DELETE").map((r) => r.path)).toEqual(["/users/user-2"]);
  });

  it("does nothing when cancelled", async () => {
    const requests = fakeApi(...signedInAs("admin"), usersApi([me, grace]));
    renderRoute("/admin/users");

    fireEvent.click(await screen.findByRole("button", { name: "Delete Grace Hopper" }));
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).toBeNull();
    });
    expect(userRequests(requests, "DELETE")).toHaveLength(0);
    expect(screen.getByText("Grace Hopper")).toBeInTheDocument();
  });

  it("keeps the dialog open and explains a failure", async () => {
    fakeApi(
      ...signedInAs("admin"),
      (request) =>
        request.method === "DELETE"
          ? problem(404, "user-not-found", { detail: "No user with that id exists." })
          : undefined,
      usersApi([me, grace]),
    );
    renderRoute("/admin/users");

    fireEvent.click(await screen.findByRole("button", { name: "Delete Grace Hopper" }));
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete user" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "No user with that id exists.",
    );
  });
});
