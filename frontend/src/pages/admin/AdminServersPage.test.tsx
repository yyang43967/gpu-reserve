import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PresignResponse, Server } from "@/lib/api-types";
import {
  fakeApi,
  healthy,
  makeLab,
  makeServer,
  on,
  page,
  problem,
  signedInAs,
  type FakeRequest,
} from "@/test/fake-api";
import { renderRoute } from "@/test/render";

const MIB = 1024 * 1024;
const STORAGE = "http://storage.test";

const vision = makeLab({ id: "lab-1", name: "Vision Lab" });
const nlp = makeLab({ id: "lab-2", name: "NLP Lab" });
const closed = makeLab({ id: "lab-3", name: "Closed Lab", is_active: false });

const a100 = makeServer({
  id: "server-1",
  hostname: "gpu-01",
  gpu_count: 8,
  image_url: `${STORAGE}/uploads/servers/old.png?sig=1`,
});
const rtx = makeServer({
  id: "server-2",
  hostname: "gpu-02",
  lab: { id: "lab-2", name: "NLP Lab", timezone: "America/New_York", is_active: true },
  gpu_model: "RTX_4090",
  gpu_memory_gb: 24,
  gpu_count: 2,
  status: "maintenance",
});

function labsRoute(labs = [vision, nlp, closed]) {
  return on("GET", "/labs", () => Response.json(page(labs, { page_size: 100 })));
}

/** A stateful /servers API that filters, refuses duplicate hostnames, and keeps changes. */
function serversApi(initial: Server[]) {
  const servers = [...initial];
  const taken = (hostname: string) =>
    problem(409, "hostname-taken", { detail: `A server named '${hostname}' already exists.` });

  return (request: FakeRequest): Response | undefined => {
    if (request.method === "GET" && request.path === "/servers") {
      const lab = request.query.get("lab_id");
      const status = request.query.get("status");
      const matching = servers.filter(
        (server) => (!lab || server.lab.id === lab) && (!status || server.status === status),
      );
      return Response.json(page(matching, { page_size: 24 }));
    }
    if (request.method === "POST" && request.path === "/servers") {
      const body = request.body as Partial<Server> & { lab_id: string; image_key: string | null };
      if (servers.some((server) => server.hostname === body.hostname)) {
        return taken(body.hostname ?? "");
      }
      const created = makeServer({
        ...body,
        id: `server-${String(servers.length + 10)}`,
        image_url: body.image_key ? `${STORAGE}/${body.image_key}?sig=1` : null,
      });
      servers.push(created);
      return Response.json(created, { status: 201 });
    }
    const match = /^\/servers\/([^/]+)$/.exec(request.path);
    const index = servers.findIndex((server) => server.id === match?.[1]);
    if (!match || index === -1) return undefined;
    if (request.method === "PATCH") {
      const body = request.body as Partial<Server> & { image_key?: string | null };
      const current = servers[index];
      if (!current) return undefined;
      if (
        body.hostname &&
        servers.some((s) => s.id !== current.id && s.hostname === body.hostname)
      ) {
        return taken(body.hostname);
      }
      const { image_key: imageKey, ...rest } = body;
      const updated: Server = {
        ...current,
        ...rest,
        image_url:
          imageKey === undefined
            ? current.image_url
            : imageKey === null
              ? null
              : `${STORAGE}/${imageKey}?sig=1`,
      };
      servers[index] = updated;
      return Response.json(updated);
    }
    if (request.method === "DELETE") {
      servers.splice(index, 1);
      return new Response(null, { status: 204 });
    }
    return undefined;
  };
}

const ticket: PresignResponse = {
  key: "servers/new.png",
  url: `${STORAGE}/uploads`,
  fields: { key: "servers/new.png", "Content-Type": "image/png", policy: "p" },
  max_bytes: 5 * MIB,
  expires_at: "2026-11-06T10:15:00+00:00",
};

const presign = on("POST", "/uploads/presign", () => Response.json(ticket));
const storageOk = on("POST", "/uploads", () => new Response(null, { status: 204 }));

function imageFile(size = 1000, type = "image/png") {
  const file = new File(["x"], "photo.png", { type });
  Object.defineProperty(file, "size", { value: size });
  return file;
}

function type(field: HTMLElement, value: string) {
  fireEvent.change(field, { target: { value } });
}

function chooseImage(dialog: HTMLElement, file: File) {
  fireEvent.change(within(dialog).getByLabelText("Image"), { target: { files: [file] } });
}

function requestsTo(requests: FakeRequest[], method: string, path: string) {
  return requests.filter((r) => r.method === method && r.path.startsWith(path));
}

async function findRow(hostname: string) {
  const link = await screen.findByRole("link", { name: hostname });
  const row = link.closest("tr");
  if (!row) throw new Error(`No table row for ${hostname}`);
  return row;
}

async function openEdit(hostname: string) {
  fireEvent.click(await screen.findByRole("button", { name: `Edit ${hostname}` }));
  return screen.findByRole("dialog", { name: `Edit ${hostname}` });
}

async function openCreate() {
  const button = await screen.findByRole("button", { name: "New server" });
  await waitFor(() => {
    expect(button).toBeEnabled();
  });
  fireEvent.click(button);
  return screen.findByRole("dialog", { name: "New server" });
}

describe("admin hub", () => {
  it("links to server management", async () => {
    fakeApi(...signedInAs("admin"), labsRoute(), serversApi([a100]));
    const { router } = renderRoute("/admin");

    fireEvent.click(await screen.findByRole("link", { name: /upload photos/ }));

    expect(await screen.findByRole("heading", { name: "Servers" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/admin/servers");
  });

  it("keeps students out of server management", async () => {
    fakeApi(...signedInAs("student"), healthy);
    const { router } = renderRoute("/admin/servers");

    await screen.findByRole("heading", { name: "Dashboard" });
    expect(router.state.location.pathname).toBe("/");
  });
});

describe("server list", () => {
  it("shows each server with its lab, GPUs, and status", async () => {
    fakeApi(...signedInAs("admin"), labsRoute(), serversApi([a100, rtx]));
    renderRoute("/admin/servers");

    const row = await findRow("gpu-02");
    expect(within(row).getByText("NLP Lab")).toBeInTheDocument();
    expect(within(row).getByText("2 × RTX 4090")).toBeInTheDocument();
    expect(within(row).getByText("Maintenance")).toBeInTheDocument();
  });

  it("filters by lab and status and keeps them in the URL", async () => {
    const requests = fakeApi(...signedInAs("admin"), labsRoute(), serversApi([a100, rtx]));
    const { router } = renderRoute("/admin/servers");
    await findRow("gpu-01");

    await waitFor(() => {
      expect(screen.getByLabelText("Lab")).toBeEnabled();
    });
    type(screen.getByLabelText("Lab"), "lab-2");
    type(screen.getByLabelText("Status"), "maintenance");

    await waitFor(() => {
      expect(screen.queryByRole("link", { name: "gpu-01" })).toBeNull();
    });
    expect(router.state.location.search).toBe("?lab=lab-2&status=maintenance");
    const last = requestsTo(requests, "GET", "/servers").at(-1);
    expect(last?.query.get("lab_id")).toBe("lab-2");
    expect(last?.query.get("status")).toBe("maintenance");
  });

  it("asks for an active lab before servers can be added", async () => {
    fakeApi(...signedInAs("admin"), labsRoute([closed]), serversApi([]));
    renderRoute("/admin/servers");

    expect(await screen.findByText(/Create an active lab first/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to labs" })).toHaveAttribute("href", "/admin/labs");
    expect(screen.getByRole("button", { name: "New server" })).toBeDisabled();
  });

  it("shows an error with retry", async () => {
    let fail = true;
    fakeApi(
      ...signedInAs("admin"),
      labsRoute(),
      (request) =>
        fail && request.path === "/servers" ? problem(500, "internal-error") : undefined,
      serversApi([a100]),
    );
    renderRoute("/admin/servers");

    expect(await screen.findByText("Could not load servers")).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await findRow("gpu-01")).toBeInTheDocument();
  });
});

describe("creating a server", () => {
  it("validates before sending anything", async () => {
    const requests = fakeApi(...signedInAs("admin"), labsRoute(), serversApi([]));
    renderRoute("/admin/servers");
    const dialog = await openCreate();

    type(within(dialog).getByLabelText("Hostname"), "-gpu");
    type(within(dialog).getByLabelText("GPU count"), "17");
    type(within(dialog).getByLabelText("Longest booking (hours)"), "0");
    fireEvent.click(within(dialog).getByRole("button", { name: "Create server" }));

    expect(
      await within(dialog).findByText(
        "Use letters, digits, and hyphens, not starting or ending with a hyphen.",
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("Enter a whole number from 1 to 16.")).toBeInTheDocument();
    expect(
      within(dialog).getByText("Enter a whole number from 1 to 336 hours."),
    ).toBeInTheDocument();
    expect(requestsTo(requests, "POST", "/servers")).toHaveLength(0);
  });

  it("previews the stored hostname and the memory per GPU", async () => {
    fakeApi(...signedInAs("admin"), labsRoute(), serversApi([]));
    renderRoute("/admin/servers");
    const dialog = await openCreate();

    type(within(dialog).getByLabelText("Hostname"), " GPU-07 ");
    expect(within(dialog).getByText("gpu-07")).toBeInTheDocument();

    expect(within(dialog).getByText("80 GB per GPU")).toBeInTheDocument();
    type(within(dialog).getByLabelText("GPU model"), "RTX_4090");
    expect(within(dialog).getByText("24 GB per GPU")).toBeInTheDocument();
  });

  it("offers only active labs", async () => {
    fakeApi(...signedInAs("admin"), labsRoute(), serversApi([]));
    renderRoute("/admin/servers");
    const dialog = await openCreate();

    const lab = within(dialog).getByLabelText("Lab");
    expect(
      within(lab)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["Vision Lab", "NLP Lab"]);
  });

  it("creates the server and shows it in the list", async () => {
    const requests = fakeApi(...signedInAs("admin"), labsRoute(), serversApi([]));
    renderRoute("/admin/servers");
    const dialog = await openCreate();

    type(within(dialog).getByLabelText("Lab"), "lab-2");
    type(within(dialog).getByLabelText("Hostname"), "NLP-H100-01");
    type(within(dialog).getByLabelText("GPU model"), "H100_80GB");
    type(within(dialog).getByLabelText("GPU count"), "4");
    fireEvent.click(within(dialog).getByRole("switch", { name: "Requires approval" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Create server" }));

    expect(await screen.findByText("Created nlp-h100-01.")).toBeInTheDocument();
    expect(await findRow("nlp-h100-01")).toBeInTheDocument();
    expect(requestsTo(requests, "POST", "/servers")[0]?.body).toEqual({
      lab_id: "lab-2",
      hostname: "nlp-h100-01",
      gpu_model: "H100_80GB",
      gpu_count: 4,
      cpu_cores: 32,
      ram_gb: 256,
      interconnect: null,
      status: "available",
      requires_approval: true,
      max_booking_hours: 12,
      notes: null,
      image_key: null,
    });
  });

  it("puts a taken hostname under the hostname field", async () => {
    fakeApi(...signedInAs("admin"), labsRoute(), serversApi([a100]));
    renderRoute("/admin/servers");
    const dialog = await openCreate();

    type(within(dialog).getByLabelText("Hostname"), "GPU-01");
    fireEvent.click(within(dialog).getByRole("button", { name: "Create server" }));

    expect(
      await within(dialog).findByText("A server named 'gpu-01' already exists."),
    ).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Hostname")).toHaveAttribute("aria-invalid", "true");
  });

  it("creates the server with an uploaded image", async () => {
    const requests = fakeApi(
      ...signedInAs("admin"),
      labsRoute(),
      presign,
      storageOk,
      serversApi([]),
    );
    renderRoute("/admin/servers");
    const dialog = await openCreate();

    type(within(dialog).getByLabelText("Hostname"), "gpu-09");
    chooseImage(dialog, imageFile());
    await within(dialog).findByText("PNG, JPEG, or WebP.");
    fireEvent.click(within(dialog).getByRole("button", { name: "Create server" }));

    await screen.findByText("Created gpu-09.");
    expect(requestsTo(requests, "POST", "/uploads/presign")[0]?.body).toEqual({
      purpose: "server_image",
      content_type: "image/png",
      size_bytes: 1000,
    });
    expect(requestsTo(requests, "POST", "/servers")[0]?.body).toMatchObject({
      image_key: "servers/new.png",
    });
  });
});

describe("editing a server", () => {
  it("sends only the changed fields", async () => {
    const requests = fakeApi(...signedInAs("admin"), labsRoute(), serversApi([a100]));
    renderRoute("/admin/servers");
    const dialog = await openEdit("gpu-01");
    const save = within(dialog).getByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled();

    type(within(dialog).getByLabelText("Status"), "maintenance");
    type(within(dialog).getByLabelText("Notes"), "Down for a BIOS update.");
    fireEvent.click(save);

    expect(await screen.findByText("Saved changes to gpu-01.")).toBeInTheDocument();
    expect(requestsTo(requests, "PATCH", "/servers")[0]?.body).toEqual({
      status: "maintenance",
      notes: "Down for a BIOS update.",
    });
  });

  it("explains that lowering the GPU count removes the highest-numbered GPUs", async () => {
    fakeApi(...signedInAs("admin"), labsRoute(), serversApi([a100]));
    renderRoute("/admin/servers");
    const dialog = await openEdit("gpu-01");
    const count = within(dialog).getByLabelText("GPU count");

    type(count, "4");
    expect(
      within(dialog).getByText(/removes the 4 highest-numbered GPUs \(GPU 4–7\)/),
    ).toBeInTheDocument();

    type(count, "7");
    expect(
      within(dialog).getByText(/removes the highest-numbered GPU \(GPU 7\)/),
    ).toBeInTheDocument();

    type(count, "12");
    expect(within(dialog).queryByText(/highest-numbered/)).toBeNull();
  });

  it("puts a taken hostname under the hostname field", async () => {
    fakeApi(...signedInAs("admin"), labsRoute(), serversApi([a100, rtx]));
    renderRoute("/admin/servers");
    const dialog = await openEdit("gpu-02");

    type(within(dialog).getByLabelText("Hostname"), "gpu-01");
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));

    expect(
      await within(dialog).findByText("A server named 'gpu-01' already exists."),
    ).toBeInTheDocument();
  });

  it("keeps an inactive lab selectable for a server already in it", async () => {
    const inClosed = makeServer({
      id: "server-3",
      hostname: "old-01",
      lab: { id: "lab-3", name: "Closed Lab", timezone: "America/Los_Angeles", is_active: false },
    });
    fakeApi(...signedInAs("admin"), labsRoute(), serversApi([inClosed]));
    renderRoute("/admin/servers");
    const dialog = await openEdit("old-01");

    expect(within(dialog).getByLabelText("Lab")).toHaveValue("lab-3");
    expect(
      within(dialog).getByRole("option", { name: "Closed Lab (inactive)" }),
    ).toBeInTheDocument();
  });
});

describe("server images", () => {
  it("shows the current image", async () => {
    fakeApi(...signedInAs("admin"), labsRoute(), serversApi([a100]));
    renderRoute("/admin/servers");
    const dialog = await openEdit("gpu-01");

    expect(within(dialog).getByRole("img", { name: "The gpu-01 server" })).toHaveAttribute(
      "src",
      a100.image_url,
    );
  });

  it("checks the file before asking to upload it", async () => {
    const requests = fakeApi(...signedInAs("admin"), labsRoute(), presign, serversApi([a100]));
    renderRoute("/admin/servers");
    const dialog = await openEdit("gpu-01");

    chooseImage(dialog, imageFile(5 * MIB + 1));
    expect(
      await within(dialog).findByText("The image must be 5 MiB or smaller."),
    ).toBeInTheDocument();

    chooseImage(dialog, imageFile(1000, "image/gif"));
    expect(
      await within(dialog).findByText("Choose a PNG, JPEG, or WebP image."),
    ).toBeInTheDocument();

    expect(requestsTo(requests, "POST", "/uploads")).toHaveLength(0);
  });

  it("disables saving until the upload finishes, then saves the new key", async () => {
    let finish: (response: Response) => void = () => undefined;
    const requests = fakeApi(
      ...signedInAs("admin"),
      labsRoute(),
      presign,
      on("POST", "/uploads", () => new Promise<Response>((resolve) => (finish = resolve))),
      serversApi([a100]),
    );
    renderRoute("/admin/servers");
    const dialog = await openEdit("gpu-01");
    const save = within(dialog).getByRole("button", { name: "Save changes" });

    type(within(dialog).getByLabelText("Notes"), "New photo.");
    chooseImage(dialog, imageFile());

    expect(await within(dialog).findByText("Uploading…")).toBeInTheDocument();
    expect(save).toBeDisabled();

    finish(new Response(null, { status: 204 }));
    await waitFor(() => {
      expect(save).toBeEnabled();
    });
    fireEvent.click(save);

    await screen.findByText("Saved changes to gpu-01.");
    expect(requestsTo(requests, "PATCH", "/servers")[0]?.body).toEqual({
      notes: "New photo.",
      image_key: "servers/new.png",
    });
  });

  it("shows a storage failure as a readable message and keeps the old image", async () => {
    fakeApi(
      ...signedInAs("admin"),
      labsRoute(),
      presign,
      on(
        "POST",
        "/uploads",
        () =>
          new Response("<?xml version='1.0'?><Error><Code>EntityTooLarge</Code></Error>", {
            status: 400,
            headers: { "Content-Type": "application/xml" },
          }),
      ),
      serversApi([a100]),
    );
    renderRoute("/admin/servers");
    const dialog = await openEdit("gpu-01");

    chooseImage(dialog, imageFile());

    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("The upload failed (status 400). Try again.");
    expect(alert.textContent).not.toMatch(/<|EntityTooLarge/);
    expect(within(dialog).getByRole("img", { name: "The gpu-01 server" })).toHaveAttribute(
      "src",
      a100.image_url,
    );
    expect(within(dialog).getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  it("shows an API refusal to sign the upload", async () => {
    fakeApi(
      ...signedInAs("admin"),
      labsRoute(),
      on("POST", "/uploads/presign", () =>
        problem(422, "invalid-upload", { detail: "The file is larger than 5 MiB." }),
      ),
      serversApi([a100]),
    );
    renderRoute("/admin/servers");
    const dialog = await openEdit("gpu-01");

    chooseImage(dialog, imageFile());

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "The file is larger than 5 MiB.",
    );
  });

  it("removes the image by sending null", async () => {
    const requests = fakeApi(...signedInAs("admin"), labsRoute(), serversApi([a100]));
    renderRoute("/admin/servers");
    const dialog = await openEdit("gpu-01");

    fireEvent.click(within(dialog).getByRole("button", { name: "Remove image" }));
    expect(within(dialog).queryByRole("img")).toBeNull();
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await screen.findByText("Saved changes to gpu-01.");
    expect(requestsTo(requests, "PATCH", "/servers")[0]?.body).toEqual({ image_key: null });
  });

  it("does not count removing a missing image as a change", async () => {
    fakeApi(...signedInAs("admin"), labsRoute(), presign, storageOk, serversApi([rtx]));
    renderRoute("/admin/servers");
    const dialog = await openEdit("gpu-02");

    chooseImage(dialog, imageFile());
    const remove = await within(dialog).findByRole("button", { name: "Remove image" });
    await within(dialog).findByText("PNG, JPEG, or WebP.");
    fireEvent.click(remove);

    expect(within(dialog).getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  it("releases preview URLs when replaced and when the dialog closes", async () => {
    const created: string[] = [];
    vi.spyOn(URL, "createObjectURL").mockImplementation(() => {
      const url = `blob:preview/${String(created.length)}`;
      created.push(url);
      return url;
    });
    const revoke = vi.spyOn(URL, "revokeObjectURL");
    fakeApi(...signedInAs("admin"), labsRoute(), presign, storageOk, serversApi([a100]));
    renderRoute("/admin/servers");
    const dialog = await openEdit("gpu-01");

    chooseImage(dialog, imageFile());
    await waitFor(() => {
      expect(within(dialog).getByRole("img")).toHaveAttribute("src", "blob:preview/0");
    });
    await within(dialog).findByText("PNG, JPEG, or WebP.");

    chooseImage(dialog, imageFile());
    await waitFor(() => {
      expect(within(dialog).getByRole("img")).toHaveAttribute("src", "blob:preview/1");
    });
    await within(dialog).findByText("PNG, JPEG, or WebP.");
    expect(revoke).toHaveBeenCalledWith("blob:preview/0");
    expect(revoke).not.toHaveBeenCalledWith("blob:preview/1");

    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(revoke).toHaveBeenCalledWith("blob:preview/1");
  });
});

describe("deleting a server", () => {
  it("asks for confirmation, then deletes", async () => {
    const requests = fakeApi(...signedInAs("admin"), labsRoute(), serversApi([a100, rtx]));
    renderRoute("/admin/servers");

    fireEvent.click(await screen.findByRole("button", { name: "Delete gpu-02" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete gpu-02?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete server" }));

    expect(await screen.findByText("Deleted gpu-02.")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole("link", { name: "gpu-02" })).toBeNull();
    });
    expect(requestsTo(requests, "DELETE", "/servers")).toHaveLength(1);
  });

  it("keeps the dialog open with the error when deleting fails", async () => {
    fakeApi(
      ...signedInAs("admin"),
      labsRoute(),
      on("DELETE", "/servers/server-1", () =>
        problem(409, "gpus-in-use", { detail: "Some GPUs have upcoming reservations." }),
      ),
      serversApi([a100]),
    );
    renderRoute("/admin/servers");

    fireEvent.click(await screen.findByRole("button", { name: "Delete gpu-01" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete gpu-01?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete server" }));

    expect(
      await within(dialog).findByText("Some GPUs have upcoming reservations."),
    ).toBeInTheDocument();
  });
});
