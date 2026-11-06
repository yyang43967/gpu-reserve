import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { z } from "zod";

import {
  createUser,
  DEFAULT_WEEKLY_GPU_HOUR_QUOTA,
  deleteUser,
  MAX_WEEKLY_GPU_HOUR_QUOTA,
  updateUser,
  type UpdateUserInput,
} from "@/admin/users-api";
import { FormAlert } from "@/components/form-alert";
import { FormField, SelectField } from "@/components/form-field";
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
import type { User } from "@/lib/api-types";
import { applyServerErrors, describeError } from "@/lib/form-errors";
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH } from "@/lib/validation";

const roleSchema = z.enum(["student", "admin"]);

const quotaSchema = z
  .number({ error: "Enter a whole number of hours." })
  .int({ error: "Enter a whole number of hours." })
  .min(0, { error: "The quota cannot be negative." })
  .max(MAX_WEEKLY_GPU_HOUR_QUOTA, {
    error: `Use at most ${MAX_WEEKLY_GPU_HOUR_QUOTA.toLocaleString("en-US")} hours.`,
  });

const QUOTA_DESCRIPTION = "GPU-hours per week.";

function RoleOptions() {
  return (
    <>
      <option value="student">Student</option>
      <option value="admin">Admin</option>
    </>
  );
}

// --- create ---

const createSchema = z.object({
  full_name: z
    .string()
    .trim()
    .min(1, { error: "Enter a name." })
    .max(200, { error: "Use at most 200 characters." }),
  email: z.email({ error: "Enter a valid email address." }),
  password: z
    .string()
    .min(PASSWORD_MIN_LENGTH, {
      error: `Use at least ${String(PASSWORD_MIN_LENGTH)} characters.`,
    })
    .max(PASSWORD_MAX_LENGTH, {
      error: `Use at most ${String(PASSWORD_MAX_LENGTH)} characters.`,
    }),
  role: roleSchema,
  weekly_gpu_hour_quota: quotaSchema,
});

type CreateValues = z.infer<typeof createSchema>;

function CreateUserForm({ onCreated }: { onCreated: (user: User) => void }) {
  const [formError, setFormError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<CreateValues>({
    resolver: zodResolver(createSchema),
    defaultValues: {
      full_name: "",
      email: "",
      password: "",
      role: "student",
      weekly_gpu_hour_quota: DEFAULT_WEEKLY_GPU_HOUR_QUOTA,
    },
  });

  const onSubmit = handleSubmit(async (values) => {
    setFormError(null);
    try {
      onCreated(await createUser(values));
    } catch (error) {
      setFormError(
        applyServerErrors(error, setError, [
          "full_name",
          "email",
          "password",
          "role",
          "weekly_gpu_hour_quota",
        ]),
      );
    }
  });

  return (
    <form noValidate onSubmit={(event) => void onSubmit(event)} className="grid gap-4">
      <FormAlert message={formError} />
      <FormField
        label="Full name"
        autoComplete="off"
        error={errors.full_name?.message}
        {...register("full_name")}
      />
      <FormField
        label="Email"
        type="email"
        autoComplete="off"
        error={errors.email?.message}
        {...register("email")}
      />
      <FormField
        label="Initial password"
        type="password"
        autoComplete="new-password"
        description="Share it with the user privately. They can change it on their account page."
        error={errors.password?.message}
        {...register("password")}
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <SelectField label="Role" error={errors.role?.message} {...register("role")}>
          <RoleOptions />
        </SelectField>
        <FormField
          label="Weekly quota"
          type="number"
          inputMode="numeric"
          min={0}
          max={MAX_WEEKLY_GPU_HOUR_QUOTA}
          step={1}
          description={QUOTA_DESCRIPTION}
          error={errors.weekly_gpu_hour_quota?.message}
          {...register("weekly_gpu_hour_quota", { valueAsNumber: true })}
        />
      </div>
      <DialogFooter>
        <Button type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Creating…" : "Create user"}
        </Button>
      </DialogFooter>
    </form>
  );
}

export function CreateUserDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (user: User) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create user</DialogTitle>
          <DialogDescription>The account can sign in right away.</DialogDescription>
        </DialogHeader>
        {/* Content unmounts when closed, so every opening starts with an empty form. */}
        <CreateUserForm onCreated={onCreated} />
      </DialogContent>
    </Dialog>
  );
}

// --- edit ---

const editSchema = z.object({
  role: roleSchema,
  is_active: z.boolean(),
  weekly_gpu_hour_quota: quotaSchema,
});

type EditValues = z.infer<typeof editSchema>;

function EditUserForm({
  user,
  isSelf,
  onSaved,
}: {
  user: User;
  isSelf: boolean;
  onSaved: (user: User) => void;
}) {
  const [formError, setFormError] = useState<string | null>(null);
  const {
    register,
    control,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting, isDirty, dirtyFields },
  } = useForm<EditValues>({
    resolver: zodResolver(editSchema),
    defaultValues: {
      role: user.role,
      is_active: user.is_active,
      weekly_gpu_hour_quota: user.weekly_gpu_hour_quota,
    },
  });

  const onSubmit = handleSubmit(async (values) => {
    setFormError(null);
    // Send only what changed, so the request states exactly what the admin did.
    const changes: UpdateUserInput = {};
    if (dirtyFields.role) changes.role = values.role;
    if (dirtyFields.is_active) changes.is_active = values.is_active;
    if (dirtyFields.weekly_gpu_hour_quota) {
      changes.weekly_gpu_hour_quota = values.weekly_gpu_hour_quota;
    }
    try {
      onSaved(await updateUser(user.id, changes));
    } catch (error) {
      setFormError(applyServerErrors(error, setError, ["role", "weekly_gpu_hour_quota"]));
    }
  });

  return (
    <form noValidate onSubmit={(event) => void onSubmit(event)} className="grid gap-4">
      <FormAlert message={formError} />
      {isSelf && (
        <p className="text-sm text-muted-foreground">
          You cannot change your own role or deactivate yourself. Ask another admin.
        </p>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <SelectField
          label="Role"
          disabled={isSelf}
          error={errors.role?.message}
          {...register("role")}
        >
          <RoleOptions />
        </SelectField>
        <FormField
          label="Weekly quota"
          type="number"
          inputMode="numeric"
          min={0}
          max={MAX_WEEKLY_GPU_HOUR_QUOTA}
          step={1}
          description={QUOTA_DESCRIPTION}
          error={errors.weekly_gpu_hour_quota?.message}
          {...register("weekly_gpu_hour_quota", { valueAsNumber: true })}
        />
      </div>
      <Controller
        control={control}
        name="is_active"
        render={({ field }) => (
          <div className="flex items-start gap-3">
            <Switch
              id="user-active"
              checked={field.value}
              onCheckedChange={field.onChange}
              onBlur={field.onBlur}
              disabled={isSelf}
              aria-describedby="user-active-description"
            />
            <div className="grid gap-1">
              <Label htmlFor="user-active">Active</Label>
              <p id="user-active-description" className="text-xs text-muted-foreground">
                Inactive users cannot sign in. Deactivating or changing the role signs the user out
                everywhere.
              </p>
            </div>
          </div>
        )}
      />
      <DialogFooter>
        <Button type="submit" disabled={isSubmitting || !isDirty}>
          {isSubmitting ? "Saving…" : "Save changes"}
        </Button>
      </DialogFooter>
    </form>
  );
}

export function EditUserDialog({
  user,
  isSelf,
  onOpenChange,
  onSaved,
}: {
  user: User | null;
  isSelf: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: (user: User) => void;
}) {
  return (
    <Dialog open={user !== null} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit {user?.full_name}</DialogTitle>
          <DialogDescription className="[overflow-wrap:anywhere]">{user?.email}</DialogDescription>
        </DialogHeader>
        {user && <EditUserForm key={user.id} user={user} isSelf={isSelf} onSaved={onSaved} />}
      </DialogContent>
    </Dialog>
  );
}

// --- delete ---

export function DeleteUserDialog({
  user,
  onOpenChange,
  onDeleted,
}: {
  user: User | null;
  onOpenChange: (open: boolean) => void;
  onDeleted: (user: User) => void;
}) {
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const changeOpen = (open: boolean) => {
    if (deleting) return;
    if (!open) setError(null);
    onOpenChange(open);
  };

  const confirm = async () => {
    if (!user) return;
    setDeleting(true);
    setError(null);
    try {
      await deleteUser(user.id);
      onDeleted(user);
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <AlertDialog open={user !== null} onOpenChange={changeOpen}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete {user?.full_name}?</AlertDialogTitle>
          <AlertDialogDescription>
            This permanently removes the account for {user?.email} and signs them out. To keep the
            account but block sign-in, deactivate it instead.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <FormAlert message={error} />
        <AlertDialogFooter>
          <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
          {/* A plain button, not AlertDialogAction, so the dialog stays open on failure. */}
          <Button variant="destructive" disabled={deleting} onClick={() => void confirm()}>
            {deleting ? "Deleting…" : "Delete user"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
