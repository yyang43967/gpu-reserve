import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Lab } from "@/lib/api-types";
import {
  fakeApi,
  healthy,
  makeLab,
  on,
  page,
  problem,
  signedInAs,
  type FakeRequest,
} from "@/test/fake-api";
import { renderRoute } from "@/test/render";

const vision = makeLab({
  id: "lab-1",
  name: "Vision Lab",
  location: "Gates 104",
  description: "Cameras and big GPUs.",
});
const nlp = makeLab({
  id: "lab-2",
  name: "NLP Lab",
  location: "Huang 210",
  timezone: "America/New_York",
});
const old = makeLab({ id: "lab-3", name: "Old Lab", location: "Basement", is_active: false });

/**
 * A stateful /labs API: filters by status, refuses duplicate names ignoring case, and
 * refuses to delete labs listed in `withServers`.
 */
function labsApi(initial: Lab[], withServers: string[] = []) {
  const labs = [...initial];
  const nameTaken = (name: string, except?: string) =>
    labs.some((lab) => lab.id !== except && lab.name.toLowerCase() === name.toLowerCase());
  const taken = (name: string) =>
    problem(409, "lab-name-taken", { detail: `A lab named '${name}' already exists.` });

  return (request: FakeRequest): Response | undefined => {
    if (request.method === "GET" && request.path === "/labs") {
      const active = request.query.get("is_active");
      const matching = labs.filter((lab) => !active || String(lab.is_active) === active);
      return Response.json(page(matching, { page_size: 100 }));
    }
    if (request.method === "POST" && request.path === "/labs") {
      const body = request.body as Lab;
      if (nameTaken(body.name)) return taken(body.name);
      const created = makeLab({ ...body, id: `lab-${String(labs.length + 10)}` });
      labs.push(created);
      return Response.json(created, { status: 201 });
    }
    const match = /^\/labs\/([^/]+)$/.exec(request.path);
    const index = labs.findIndex((lab) => lab.id === match?.[1]);
    if (!match || index === -1) return undefined;
    if (request.method === "PATCH") {
      const body = request.body as Partial<Lab>;
      if (body.name && nameTaken(body.name, match[1])) return taken(body.name);
      const updated = { ...labs[index], ...body } as Lab;
      labs[index] = updated;
      return Response.json(updated);
    }
    if (request.method === "DELETE") {
      if (withServers.includes(match[1] ?? "")) {
        return problem(409, "lab-not-empty", {
          detail: "Remove or move this lab's servers first, or deactivate the lab.",
        });
      }
      labs.splice(index, 1);
      return new Response(null, { status: 204 });
    }
    return undefined;
  };
}

function type(field: HTMLElement, value: string) {
  fireEvent.change(field, { target: { value } });
}

function labRequests(requests: FakeRequest[], method = "GET") {
  return requests.filter((r) => r.method === method && r.path.startsWith("/labs"));
}

async function findRow(name: string) {
  const cell = await screen.findByText(name);
  const row = cell.closest("tr");
  if (!row) throw new Error(`No table row for ${name}`);
  return row;
}

describe("admin hub", () => {
  it("links to lab management", async () => {
    fakeApi(...signedInAs("admin"), labsApi([vision]));
    const { router } = renderRoute("/admin");

    fireEvent.click(await screen.findByRole("link", { name: /Labs/ }));

    expect(await screen.findByRole("heading", { name: "Labs" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/admin/labs");
  });

  it("keeps students out of lab management", async () => {
    const requests = fakeApi(...signedInAs("student"), healthy);
    const { router } = renderRoute("/admin/labs");

    await screen.findByRole("heading", { name: "Dashboard" });
    expect(router.state.location.pathname).toBe("/");
    expect(labRequests(requests)).toHaveLength(0);
  });
});

describe("lab list", () => {
  it("shows a loading state, then the labs", async () => {
    fakeApi(...signedInAs("admin"), labsApi([vision, nlp, old]));
    renderRoute("/admin/labs");

    expect(await screen.findByText("Loading labs")).toBeInTheDocument();
    const row = await findRow("Vision Lab");
    expect(within(row).getByText("Gates 104")).toBeInTheDocument();
    expect(within(row).getByText("Cameras and big GPUs.")).toBeInTheDocument();
    expect(within(row).getByText("America/Los_Angeles")).toBeInTheDocument();
    expect(within(row).getByText("Active")).toBeInTheDocument();
    expect(within(await findRow("Old Lab")).getByText("Inactive")).toBeInTheDocument();
  });

  it("filters by status and keeps it in the URL", async () => {
    const requests = fakeApi(...signedInAs("admin"), labsApi([vision, old]));
    const { router } = renderRoute("/admin/labs");
    await findRow("Vision Lab");

    type(screen.getByLabelText("Status"), "inactive");

    await waitFor(() => {
      expect(screen.queryByText("Vision Lab")).toBeNull();
    });
    expect(screen.getByText("Old Lab")).toBeInTheDocument();
    expect(router.state.location.search).toBe("?status=inactive");
    expect(labRequests(requests).at(-1)?.query.get("is_active")).toBe("false");
  });

  it("offers to show all labs when the filter matches none", async () => {
    fakeApi(...signedInAs("admin"), labsApi([vision]));
    const { router } = renderRoute("/admin/labs?status=inactive");

    expect(await screen.findByText("No inactive labs")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show all labs" }));

    expect(await findRow("Vision Lab")).toBeInTheDocument();
    expect(router.state.location.search).toBe("");
  });

  it("shows an empty state without labs", async () => {
    fakeApi(...signedInAs("admin"), labsApi([]));
    renderRoute("/admin/labs");

    expect(await screen.findByText("No labs yet")).toBeInTheDocument();
  });

  it("shows an error with retry", async () => {
    let fail = true;
    fakeApi(
      ...signedInAs("admin"),
      (request) =>
        fail && request.path === "/labs"
          ? problem(500, "internal-error", { detail: "The server failed." })
          : undefined,
      labsApi([vision]),
    );
    renderRoute("/admin/labs");

    expect(await screen.findByText("Could not load labs")).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await findRow("Vision Lab")).toBeInTheDocument();
  });
});

describe("creating a lab", () => {
  async function openCreate() {
    fireEvent.click(await screen.findByRole("button", { name: "New lab" }));
    return screen.findByRole("dialog", { name: "New lab" });
  }

  it("validates before sending anything", async () => {
    const requests = fakeApi(...signedInAs("admin"), labsApi([]));
    renderRoute("/admin/labs");
    const dialog = await openCreate();

    type(within(dialog).getByLabelText("Time zone"), "Mars/Olympus_Mons");
    fireEvent.click(within(dialog).getByRole("button", { name: "Create lab" }));

    expect(await within(dialog).findByText("Enter a name.")).toBeInTheDocument();
    expect(within(dialog).getByText("Enter a location.")).toBeInTheDocument();
    expect(
      within(dialog).getByText("Use a time zone name such as America/Los_Angeles."),
    ).toBeInTheDocument();
    expect(labRequests(requests, "POST")).toHaveLength(0);
  });

  it("creates the lab with defaults and shows it in the list", async () => {
    const requests = fakeApi(...signedInAs("admin"), labsApi([vision]));
    renderRoute("/admin/labs");
    const dialog = await openCreate();

    expect(within(dialog).getByLabelText("Time zone")).toHaveValue("America/Los_Angeles");
    type(within(dialog).getByLabelText("Name"), "  Robotics Lab ");
    type(within(dialog).getByLabelText("Location"), "Packard 3rd floor");
    fireEvent.click(within(dialog).getByRole("button", { name: "Create lab" }));

    expect(await screen.findByText("Created Robotics Lab.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(await findRow("Robotics Lab")).toBeInTheDocument();
    expect(labRequests(requests, "POST")[0]?.body).toEqual({
      name: "Robotics Lab",
      location: "Packard 3rd floor",
      timezone: "America/Los_Angeles",
      description: null,
      is_active: true,
    });
  });

  it("puts a taken name under the name field", async () => {
    fakeApi(...signedInAs("admin"), labsApi([vision]));
    renderRoute("/admin/labs");
    const dialog = await openCreate();

    type(within(dialog).getByLabelText("Name"), "vision lab");
    type(within(dialog).getByLabelText("Location"), "Elsewhere");
    fireEvent.click(within(dialog).getByRole("button", { name: "Create lab" }));

    expect(
      await within(dialog).findByText("A lab named 'vision lab' already exists."),
    ).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Name")).toHaveAttribute("aria-invalid", "true");
  });

  it("puts API field errors under their fields", async () => {
    fakeApi(
      ...signedInAs("admin"),
      on("POST", "/labs", () =>
        problem(422, "validation-error", {
          errors: [
            {
              field: "timezone",
              message: "must be an IANA time zone name, such as America/Los_Angeles",
            },
          ],
        }),
      ),
      labsApi([]),
    );
    renderRoute("/admin/labs");
    const dialog = await openCreate();

    type(within(dialog).getByLabelText("Name"), "Robotics Lab");
    type(within(dialog).getByLabelText("Location"), "Packard");
    fireEvent.click(within(dialog).getByRole("button", { name: "Create lab" }));

    expect(
      await within(dialog).findByText(
        "must be an IANA time zone name, such as America/Los_Angeles",
      ),
    ).toBeInTheDocument();
  });

  it("suggests only time zones whose offsets are whole half hours", async () => {
    fakeApi(...signedInAs("admin"), labsApi([]));
    renderRoute("/admin/labs");
    const dialog = await openCreate();

    const input = within(dialog).getByLabelText("Time zone");
    const listId = input.getAttribute("list") ?? "";
    const suggestions = [...document.querySelectorAll(`#${listId} option`)].map(
      (option) => (option as HTMLOptionElement).value,
    );

    // The browser lists some zones under their older names (Asia/Calcutta for Kolkata).
    expect(suggestions).toContain("America/Los_Angeles");
    expect(suggestions).toContain("Asia/Calcutta");
    expect(suggestions).not.toContain("Asia/Katmandu");
    expect(suggestions).not.toContain("Pacific/Chatham");
  });

  it("shows the API's refusal of a typed-in zone under the field", async () => {
    const requests = fakeApi(
      ...signedInAs("admin"),
      on("POST", "/labs", () =>
        problem(422, "validation-error", {
          errors: [
            {
              field: "timezone",
              message: "time zones with offsets that are not whole half hours are not supported",
            },
          ],
        }),
      ),
      labsApi([]),
    );
    renderRoute("/admin/labs");
    const dialog = await openCreate();

    type(within(dialog).getByLabelText("Name"), "Himalaya Lab");
    type(within(dialog).getByLabelText("Location"), "Kathmandu");
    type(within(dialog).getByLabelText("Time zone"), "Asia/Kathmandu");
    fireEvent.click(within(dialog).getByRole("button", { name: "Create lab" }));

    expect(
      await within(dialog).findByText(
        "time zones with offsets that are not whole half hours are not supported",
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Time zone")).toHaveAttribute("aria-invalid", "true");
    expect(labRequests(requests, "POST")[0]?.body).toMatchObject({ timezone: "Asia/Kathmandu" });
  });
});

describe("editing a lab", () => {
  it("sends only the changed fields", async () => {
    const requests = fakeApi(...signedInAs("admin"), labsApi([vision, nlp]));
    renderRoute("/admin/labs");

    fireEvent.click(await screen.findByRole("button", { name: "Edit Vision Lab" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit Vision Lab" });
    const save = within(dialog).getByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled();

    type(within(dialog).getByLabelText("Location"), "Gates 200");
    type(within(dialog).getByLabelText("Description"), "");
    fireEvent.click(within(dialog).getByRole("switch", { name: "Active" }));
    fireEvent.click(save);

    expect(await screen.findByText("Saved changes to Vision Lab.")).toBeInTheDocument();
    expect(labRequests(requests, "PATCH")[0]?.body).toEqual({
      location: "Gates 200",
      description: null,
      is_active: false,
    });
    expect(within(await findRow("Vision Lab")).getByText("Inactive")).toBeInTheDocument();
  });

  it("puts a taken name under the name field", async () => {
    fakeApi(...signedInAs("admin"), labsApi([vision, nlp]));
    renderRoute("/admin/labs");

    fireEvent.click(await screen.findByRole("button", { name: "Edit NLP Lab" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit NLP Lab" });
    type(within(dialog).getByLabelText("Name"), "VISION LAB");
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));

    expect(
      await within(dialog).findByText("A lab named 'VISION LAB' already exists."),
    ).toBeInTheDocument();
  });
});

describe("deleting a lab", () => {
  it("asks for confirmation, then deletes an empty lab", async () => {
    const requests = fakeApi(...signedInAs("admin"), labsApi([vision, nlp]));
    renderRoute("/admin/labs");

    fireEvent.click(await screen.findByRole("button", { name: "Delete NLP Lab" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete NLP Lab?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete lab" }));

    expect(await screen.findByText("Deleted NLP Lab.")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByText("NLP Lab")).toBeNull();
    });
    expect(labRequests(requests, "DELETE")).toHaveLength(1);
  });

  it("explains a lab with servers and offers to deactivate it", async () => {
    const requests = fakeApi(...signedInAs("admin"), labsApi([vision], ["lab-1"]));
    renderRoute("/admin/labs");

    fireEvent.click(await screen.findByRole("button", { name: "Delete Vision Lab" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete Vision Lab?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete lab" }));

    expect(
      await within(dialog).findByText(
        "Remove or move this lab's servers first, or deactivate the lab.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Deactivate instead" }));

    expect(await screen.findByText("Deactivated Vision Lab.")).toBeInTheDocument();
    expect(labRequests(requests, "PATCH")[0]?.body).toEqual({ is_active: false });
    expect(within(await findRow("Vision Lab")).getByText("Inactive")).toBeInTheDocument();
  });

  it("does not offer to deactivate a lab that is already inactive", async () => {
    fakeApi(...signedInAs("admin"), labsApi([old], ["lab-3"]));
    renderRoute("/admin/labs");

    fireEvent.click(await screen.findByRole("button", { name: "Delete Old Lab" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete Old Lab?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete lab" }));

    await within(dialog).findByRole("alert");
    expect(within(dialog).queryByRole("button", { name: "Deactivate instead" })).toBeNull();
    expect(within(dialog).getByRole("button", { name: "Delete lab" })).toBeDisabled();
  });

  it("starts fresh when the dialog is opened again", async () => {
    fakeApi(...signedInAs("admin"), labsApi([vision], ["lab-1"]));
    renderRoute("/admin/labs");

    fireEvent.click(await screen.findByRole("button", { name: "Delete Vision Lab" }));
    let dialog = await screen.findByRole("alertdialog", { name: "Delete Vision Lab?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete lab" }));
    await within(dialog).findByRole("alert");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).toBeNull();
    });

    fireEvent.click(screen.getByRole("button", { name: "Delete Vision Lab" }));
    dialog = await screen.findByRole("alertdialog", { name: "Delete Vision Lab?" });
    expect(within(dialog).queryByRole("alert")).toBeNull();
    expect(within(dialog).getByRole("button", { name: "Delete lab" })).toBeEnabled();
  });
});
