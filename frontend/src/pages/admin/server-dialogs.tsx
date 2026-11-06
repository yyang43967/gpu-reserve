import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { z } from "zod";

import { FormAlert } from "@/components/form-alert";
import { FormField, SelectField, TextareaField } from "@/components/form-field";
import { ImageUploadField } from "@/components/image-upload";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import type { GpuModel, Lab, Server, ServerStatus } from "@/lib/api-types";
import { applyServerErrors, describeError } from "@/lib/form-errors";
import { KEEP_IMAGE, type ImageChoice } from "@/lib/image-choice";
import {
  createServer,
  DEFAULT_MAX_BOOKING_HOURS,
  deleteServer,
  GPU_MODELS,
  HOSTNAME_MAX_LENGTH,
  INTERCONNECT_MAX_LENGTH,
  MAX_BOOKING_HOURS_LIMIT,
  MAX_CPU_CORES,
  MAX_GPUS_PER_SERVER,
  MAX_RAM_GB,
  NOTES_MAX_LENGTH,
  SERVER_STATUS_LABELS,
  updateServer,
  type CreateServerInput,
  type UpdateServerInput,
} from "@/servers/servers-api";

const GPU_MODEL_VALUES = Object.keys(GPU_MODELS) as [GpuModel, ...GpuModel[]];
const STATUS_VALUES = Object.keys(SERVER_STATUS_LABELS) as [ServerStatus, ...ServerStatus[]];
// One DNS label, checked after lowercasing as the API does.
const HOSTNAME_RE = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/;

/** The hostname as the API stores it. */
function normalizeHostname(value: string): string {
  return value.trim().toLowerCase();
}

function emptyToNull(value: string): string | null {
  return value === "" ? null : value;
}

function wholeNumber(min: number, max: number, unit: string) {
  const message = `Enter a whole number from ${String(min)} to ${max.toLocaleString("en-US")}${unit}.`;
  return z
    .number({ error: message })
    .int({ error: message })
    .min(min, { error: message })
    .max(max, { error: message });
}

const serverSchema = z.object({
  lab_id: z.string().min(1, { error: "Choose a lab." }),
  hostname: z
    .string()
    .transform(normalizeHostname)
    .pipe(
      z
        .string()
        .min(1, { error: "Enter a hostname." })
        .max(HOSTNAME_MAX_LENGTH, {
          error: `Use at most ${String(HOSTNAME_MAX_LENGTH)} characters.`,
        })
        .regex(HOSTNAME_RE, {
          error: "Use letters, digits, and hyphens, not starting or ending with a hyphen.",
        }),
    ),
  gpu_model: z.enum(GPU_MODEL_VALUES, { error: "Choose a GPU model." }),
  gpu_count: wholeNumber(1, MAX_GPUS_PER_SERVER, ""),
  cpu_cores: wholeNumber(1, MAX_CPU_CORES, ""),
  ram_gb: wholeNumber(1, MAX_RAM_GB, " GB"),
  interconnect: z
    .string()
    .trim()
    .max(INTERCONNECT_MAX_LENGTH, {
      error: `Use at most ${String(INTERCONNECT_MAX_LENGTH)} characters.`,
    }),
  status: z.enum(STATUS_VALUES),
  requires_approval: z.boolean(),
  max_booking_hours: wholeNumber(1, MAX_BOOKING_HOURS_LIMIT, " hours"),
  notes: z
    .string()
    .trim()
    .max(NOTES_MAX_LENGTH, {
      error: `Use at most ${NOTES_MAX_LENGTH.toLocaleString("en-US")} characters.`,
    }),
});

type ServerInput = z.input<typeof serverSchema>;
type ServerValues = z.output<typeof serverSchema>;

const FIELDS = [
  "lab_id",
  "hostname",
  "gpu_model",
  "gpu_count",
  "cpu_cores",
  "ram_gb",
  "interconnect",
  "status",
  "requires_approval",
  "max_booking_hours",
  "notes",
] as const;

type Field = (typeof FIELDS)[number];

/** What the form produced: the values, which fields changed, and the image choice. */
interface Submission {
  values: ServerValues;
  changed: Set<Field>;
  image: ImageChoice;
}

function GpuCountHint({ before, after }: { before: number; after: number }) {
  if (!Number.isInteger(after) || after < 1 || after >= before) return null;
  const removed = before - after;
  return (
    <p className="text-xs text-muted-foreground" role="status">
      Lowering from {before} to {after} removes the {removed === 1 ? "" : `${String(removed)} `}
      highest-numbered GPU{removed === 1 ? "" : "s"} (GPU {after}
      {removed === 1 ? "" : `–${String(before - 1)}`}).
    </p>
  );
}

function ServerForm({
  server,
  labs,
  submitLabel,
  submittingLabel,
  onSubmit,
}: {
  server: Server | null;
  labs: Lab[];
  submitLabel: string;
  submittingLabel: string;
  onSubmit: (submission: Submission) => Promise<void>;
}) {
  const [formError, setFormError] = useState<string | null>(null);
  const [image, setImage] = useState<ImageChoice>(KEEP_IMAGE);
  const [uploading, setUploading] = useState(false);
  const {
    register,
    control,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting, isDirty, dirtyFields },
  } = useForm<ServerInput, unknown, ServerValues>({
    resolver: zodResolver(serverSchema),
    defaultValues: {
      lab_id: server?.lab.id ?? labs.find((lab) => lab.is_active)?.id ?? "",
      hostname: server?.hostname ?? "",
      gpu_model: server?.gpu_model ?? "A100_80GB",
      gpu_count: server?.gpu_count ?? 8,
      cpu_cores: server?.cpu_cores ?? 32,
      ram_gb: server?.ram_gb ?? 256,
      interconnect: server?.interconnect ?? "",
      status: server?.status ?? "available",
      requires_approval: server?.requires_approval ?? false,
      max_booking_hours: server?.max_booking_hours ?? DEFAULT_MAX_BOOKING_HOURS,
      notes: server?.notes ?? "",
    },
  });

  const hostname = useWatch({ control, name: "hostname" });
  const gpuModel = useWatch({ control, name: "gpu_model" });
  const gpuCount = useWatch({ control, name: "gpu_count" });
  const normalized = normalizeHostname(hostname);
  const memoryGb = GPU_MODELS[gpuModel].memoryGb;
  const imageChanged =
    image.kind === "set" || (image.kind === "remove" && (server?.image_url ?? null) !== null);

  const submit = handleSubmit(async (values) => {
    setFormError(null);
    const changed = new Set(FIELDS.filter((field) => dirtyFields[field]));
    try {
      await onSubmit({ values, changed, image });
    } catch (error) {
      setFormError(applyServerErrors(error, setError, FIELDS));
    }
  });

  // The lab a server is in stays listed even if it is inactive.
  const labOptions = labs.filter((lab) => lab.is_active || lab.id === server?.lab.id);

  return (
    <form noValidate onSubmit={(event) => void submit(event)} className="grid gap-4">
      <FormAlert message={formError} />
      <div className="grid gap-4 sm:grid-cols-2">
        <SelectField label="Lab" error={errors.lab_id?.message} {...register("lab_id")}>
          {labOptions.map((lab) => (
            <option key={lab.id} value={lab.id}>
              {lab.is_active ? lab.name : `${lab.name} (inactive)`}
            </option>
          ))}
        </SelectField>
        <div className="grid gap-2">
          <FormField
            label="Hostname"
            autoComplete="off"
            spellCheck={false}
            error={errors.hostname?.message}
            {...register("hostname")}
          />
          {normalized !== "" && normalized !== hostname && !errors.hostname && (
            <p className="text-xs text-muted-foreground">
              Saved as <span className="font-mono">{normalized}</span>
            </p>
          )}
        </div>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <SelectField
          label="GPU model"
          description={`${String(memoryGb)} GB per GPU`}
          error={errors.gpu_model?.message}
          {...register("gpu_model")}
        >
          {GPU_MODEL_VALUES.map((model) => (
            <option key={model} value={model}>
              {GPU_MODELS[model].label}
            </option>
          ))}
        </SelectField>
        <div className="grid gap-2">
          <FormField
            label="GPU count"
            type="number"
            inputMode="numeric"
            min={1}
            max={MAX_GPUS_PER_SERVER}
            step={1}
            error={errors.gpu_count?.message}
            {...register("gpu_count", { valueAsNumber: true })}
          />
          {server && <GpuCountHint before={server.gpu_count} after={gpuCount} />}
        </div>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        <FormField
          label="CPU cores"
          type="number"
          inputMode="numeric"
          min={1}
          max={MAX_CPU_CORES}
          step={1}
          error={errors.cpu_cores?.message}
          {...register("cpu_cores", { valueAsNumber: true })}
        />
        <FormField
          label="RAM (GB)"
          type="number"
          inputMode="numeric"
          min={1}
          max={MAX_RAM_GB}
          step={1}
          error={errors.ram_gb?.message}
          {...register("ram_gb", { valueAsNumber: true })}
        />
        <FormField
          label="Interconnect"
          autoComplete="off"
          placeholder="NVLink, PCIe"
          error={errors.interconnect?.message}
          {...register("interconnect")}
        />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <SelectField label="Status" error={errors.status?.message} {...register("status")}>
          {STATUS_VALUES.map((status) => (
            <option key={status} value={status}>
              {SERVER_STATUS_LABELS[status]}
            </option>
          ))}
        </SelectField>
        <FormField
          label="Longest booking (hours)"
          type="number"
          inputMode="numeric"
          min={1}
          max={MAX_BOOKING_HOURS_LIMIT}
          step={1}
          error={errors.max_booking_hours?.message}
          {...register("max_booking_hours", { valueAsNumber: true })}
        />
      </div>
      <Controller
        control={control}
        name="requires_approval"
        render={({ field }) => (
          <div className="flex items-start gap-3">
            <Switch
              id="server-requires-approval"
              checked={field.value}
              onCheckedChange={field.onChange}
              onBlur={field.onBlur}
              aria-describedby="server-requires-approval-description"
            />
            <div className="grid gap-1">
              <Label htmlFor="server-requires-approval">Requires approval</Label>
              <p
                id="server-requires-approval-description"
                className="text-xs text-muted-foreground"
              >
                An admin approves each booking on this server.
              </p>
            </div>
          </div>
        )}
      />
      <TextareaField
        label="Notes"
        rows={3}
        description="Optional. Shown to students on the server page."
        error={errors.notes?.message}
        {...register("notes")}
      />
      <ImageUploadField
        label="Image"
        purpose="server_image"
        currentUrl={server?.image_url ?? null}
        alt={server ? `The ${server.hostname} server` : "The new server"}
        choice={image}
        onChoiceChange={setImage}
        onUploadingChange={setUploading}
      />
      <DialogFooter>
        <Button
          type="submit"
          disabled={isSubmitting || uploading || (server !== null && !isDirty && !imageChanged)}
        >
          {isSubmitting ? submittingLabel : submitLabel}
        </Button>
      </DialogFooter>
    </form>
  );
}

// --- create ---

export function CreateServerDialog({
  open,
  labs,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  labs: Lab[];
  onOpenChange: (open: boolean) => void;
  onCreated: (server: Server) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>New server</DialogTitle>
          <DialogDescription>Its GPUs are created from the count.</DialogDescription>
        </DialogHeader>
        {/* Content unmounts when closed, so every opening starts with an empty form. */}
        <ServerForm
          server={null}
          labs={labs}
          submitLabel="Create server"
          submittingLabel="Creating…"
          onSubmit={async ({ values, image }) => {
            const input: CreateServerInput = {
              ...values,
              interconnect: emptyToNull(values.interconnect),
              notes: emptyToNull(values.notes),
              image_key: image.kind === "set" ? image.key : null,
            };
            onCreated(await createServer(input));
          }}
        />
      </DialogContent>
    </Dialog>
  );
}

// --- edit ---

export function EditServerDialog({
  server,
  labs,
  onOpenChange,
  onSaved,
}: {
  server: Server | null;
  labs: Lab[];
  onOpenChange: (open: boolean) => void;
  onSaved: (server: Server) => void;
}) {
  return (
    <Dialog open={server !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Edit {server?.hostname}</DialogTitle>
          <DialogDescription>{server?.lab.name}</DialogDescription>
        </DialogHeader>
        {server && (
          <ServerForm
            key={server.id}
            server={server}
            labs={labs}
            submitLabel="Save changes"
            submittingLabel="Saving…"
            onSubmit={async ({ values, changed, image }) => {
              // Send only what changed, so the request states exactly what the admin did.
              const changes: UpdateServerInput = {};
              for (const field of changed) {
                if (field === "interconnect" || field === "notes") {
                  changes[field] = emptyToNull(values[field]);
                } else {
                  Object.assign(changes, { [field]: values[field] });
                }
              }
              if (image.kind === "set") changes.image_key = image.key;
              // Removing sends null; removing an image the server never had sends nothing.
              if (image.kind === "remove" && server.image_url !== null) changes.image_key = null;
              onSaved(await updateServer(server.id, changes));
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

// --- delete ---

export function DeleteServerDialog({
  server,
  onOpenChange,
  onDeleted,
}: {
  server: Server | null;
  onOpenChange: (open: boolean) => void;
  onDeleted: (server: Server) => void;
}) {
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const changeOpen = (open: boolean) => {
    if (deleting) return;
    if (!open) setError(null);
    onOpenChange(open);
  };

  const confirm = async () => {
    if (!server) return;
    setDeleting(true);
    setError(null);
    try {
      await deleteServer(server.id);
      onDeleted(server);
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <AlertDialog open={server !== null} onOpenChange={changeOpen}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete {server?.hostname}?</AlertDialogTitle>
          <AlertDialogDescription>
            This permanently removes the server, its GPUs, and its image. To keep it but stop
            bookings, set its status to Maintenance or Retired instead.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <FormAlert message={error} />
        <AlertDialogFooter>
          <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
          {/* A plain button, not AlertDialogAction, so the dialog stays open on failure. */}
          <Button variant="destructive" disabled={deleting} onClick={() => void confirm()}>
            {deleting ? "Deleting…" : "Delete server"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
