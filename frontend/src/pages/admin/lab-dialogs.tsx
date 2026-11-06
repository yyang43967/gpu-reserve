import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { z } from "zod";

import { FormAlert } from "@/components/form-alert";
import { FormField, TextareaField } from "@/components/form-field";
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
import {
  createLab,
  DEFAULT_LAB_TIMEZONE,
  deleteLab,
  LAB_DESCRIPTION_MAX_LENGTH,
  LAB_LOCATION_MAX_LENGTH,
  LAB_NAME_MAX_LENGTH,
  updateLab,
  type UpdateLabInput,
} from "@/labs/labs-api";
import { ApiError } from "@/lib/api";
import type { Lab } from "@/lib/api-types";
import { applyServerErrors, describeError } from "@/lib/form-errors";

/** IANA names the browser knows, offered as suggestions; the API has the final say. */
const TIME_ZONES: readonly string[] =
  typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];

function isKnownTimeZone(value: string): boolean {
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: value });
    return true;
  } catch {
    return false;
  }
}

/** An empty description is stored as no description. */
function emptyToNull(value: string): string | null {
  return value === "" ? null : value;
}

function maxLength(limit: number) {
  return { error: `Use at most ${limit.toLocaleString("en-US")} characters.` };
}

const labSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, { error: "Enter a name." })
    .max(LAB_NAME_MAX_LENGTH, maxLength(LAB_NAME_MAX_LENGTH)),
  location: z
    .string()
    .trim()
    .min(1, { error: "Enter a location." })
    .max(LAB_LOCATION_MAX_LENGTH, maxLength(LAB_LOCATION_MAX_LENGTH)),
  timezone: z
    .string()
    .trim()
    .min(1, { error: "Enter a time zone." })
    .refine(isKnownTimeZone, { error: "Use a time zone name such as America/Los_Angeles." }),
  description: z
    .string()
    .trim()
    .max(LAB_DESCRIPTION_MAX_LENGTH, maxLength(LAB_DESCRIPTION_MAX_LENGTH)),
  is_active: z.boolean(),
});

type LabValues = z.infer<typeof labSchema>;

const FIELDS = ["name", "location", "timezone", "description", "is_active"] as const;

function LabForm({
  lab,
  submitLabel,
  submittingLabel,
  onSubmit,
}: {
  lab: Lab | null;
  submitLabel: string;
  submittingLabel: string;
  /** Receives the values and which fields the admin changed. */
  onSubmit: (values: LabValues, changed: Set<keyof LabValues>) => Promise<void>;
}) {
  const [formError, setFormError] = useState<string | null>(null);
  const {
    register,
    control,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting, isDirty, dirtyFields },
  } = useForm<LabValues>({
    resolver: zodResolver(labSchema),
    defaultValues: {
      name: lab?.name ?? "",
      location: lab?.location ?? "",
      timezone: lab?.timezone ?? DEFAULT_LAB_TIMEZONE,
      description: lab?.description ?? "",
      is_active: lab?.is_active ?? true,
    },
  });

  const submit = handleSubmit(async (values) => {
    setFormError(null);
    const changed = new Set(FIELDS.filter((field) => dirtyFields[field]));
    try {
      await onSubmit(values, changed);
    } catch (error) {
      setFormError(applyServerErrors(error, setError, FIELDS));
    }
  });

  return (
    <form noValidate onSubmit={(event) => void submit(event)} className="grid gap-4">
      <FormAlert message={formError} />
      <FormField
        label="Name"
        autoComplete="off"
        description="Unique across labs, ignoring case."
        error={errors.name?.message}
        {...register("name")}
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <FormField
          label="Location"
          autoComplete="off"
          placeholder="Building and room"
          error={errors.location?.message}
          {...register("location")}
        />
        <FormField
          label="Time zone"
          autoComplete="off"
          list="lab-time-zones"
          description="Bookings show in this zone."
          error={errors.timezone?.message}
          {...register("timezone")}
        />
        <datalist id="lab-time-zones">
          {TIME_ZONES.map((zone) => (
            <option key={zone} value={zone} />
          ))}
        </datalist>
      </div>
      <TextareaField
        label="Description"
        rows={3}
        description="Optional. Shown to students."
        error={errors.description?.message}
        {...register("description")}
      />
      <Controller
        control={control}
        name="is_active"
        render={({ field }) => (
          <div className="flex items-start gap-3">
            <Switch
              id="lab-active"
              checked={field.value}
              onCheckedChange={field.onChange}
              onBlur={field.onBlur}
              aria-describedby="lab-active-description"
            />
            <div className="grid gap-1">
              <Label htmlFor="lab-active">Active</Label>
              <p id="lab-active-description" className="text-xs text-muted-foreground">
                Students do not see inactive labs or their servers.
              </p>
            </div>
          </div>
        )}
      />
      <DialogFooter>
        <Button type="submit" disabled={isSubmitting || (lab !== null && !isDirty)}>
          {isSubmitting ? submittingLabel : submitLabel}
        </Button>
      </DialogFooter>
    </form>
  );
}

// --- create ---

export function CreateLabDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (lab: Lab) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New lab</DialogTitle>
          <DialogDescription>Servers are added to a lab afterwards.</DialogDescription>
        </DialogHeader>
        {/* Content unmounts when closed, so every opening starts with an empty form. */}
        <LabForm
          lab={null}
          submitLabel="Create lab"
          submittingLabel="Creating…"
          onSubmit={async (values) => {
            onCreated(await createLab({ ...values, description: emptyToNull(values.description) }));
          }}
        />
      </DialogContent>
    </Dialog>
  );
}

// --- edit ---

export function EditLabDialog({
  lab,
  onOpenChange,
  onSaved,
}: {
  lab: Lab | null;
  onOpenChange: (open: boolean) => void;
  onSaved: (lab: Lab) => void;
}) {
  return (
    <Dialog open={lab !== null} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit {lab?.name}</DialogTitle>
          <DialogDescription>Changes apply to every server in the lab.</DialogDescription>
        </DialogHeader>
        {lab && (
          <LabForm
            key={lab.id}
            lab={lab}
            submitLabel="Save changes"
            submittingLabel="Saving…"
            onSubmit={async (values, changed) => {
              // Send only what changed, so the request states exactly what the admin did.
              const changes: UpdateLabInput = {};
              if (changed.has("name")) changes.name = values.name;
              if (changed.has("location")) changes.location = values.location;
              if (changed.has("timezone")) changes.timezone = values.timezone;
              if (changed.has("description")) {
                changes.description = emptyToNull(values.description);
              }
              if (changed.has("is_active")) changes.is_active = values.is_active;
              onSaved(await updateLab(lab.id, changes));
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

// --- delete ---

export function DeleteLabDialog({
  lab,
  onOpenChange,
  onDeleted,
  onDeactivated,
}: {
  lab: Lab | null;
  onOpenChange: (open: boolean) => void;
  onDeleted: (lab: Lab) => void;
  onDeactivated: (lab: Lab) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Set when the API refuses because the lab still has servers.
  const [hasServers, setHasServers] = useState(false);

  const changeOpen = (open: boolean) => {
    if (busy) return;
    if (!open) {
      setError(null);
      setHasServers(false);
    }
    onOpenChange(open);
  };

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      setHasServers(false);
    } catch (caught) {
      setHasServers(caught instanceof ApiError && caught.code === "lab-not-empty");
      setError(describeError(caught));
    } finally {
      setBusy(false);
    }
  };

  const confirmDelete = () =>
    run(async () => {
      if (!lab) return;
      await deleteLab(lab.id);
      onDeleted(lab);
    });

  const deactivate = () =>
    run(async () => {
      if (!lab) return;
      onDeactivated(await updateLab(lab.id, { is_active: false }));
    });

  return (
    <AlertDialog open={lab !== null} onOpenChange={changeOpen}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete {lab?.name}?</AlertDialogTitle>
          <AlertDialogDescription>
            This permanently removes the lab. Only a lab without servers can be deleted; to hide a
            lab and its servers from students, deactivate it instead.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <FormAlert message={error} />
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
          {hasServers && lab?.is_active ? (
            <Button disabled={busy} onClick={() => void deactivate()}>
              {busy ? "Deactivating…" : "Deactivate instead"}
            </Button>
          ) : (
            // A plain button, not AlertDialogAction, so the dialog stays open on failure.
            <Button
              variant="destructive"
              disabled={busy || hasServers}
              onClick={() => void confirmDelete()}
            >
              {busy ? "Deleting…" : "Delete lab"}
            </Button>
          )}
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
