import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { changePassword, updateProfile } from "@/auth/auth-api";
import { meQueryKey, useAuth } from "@/auth/auth-context";
import { useDemoLogin } from "@/auth/use-demo-login";
import { FormAlert } from "@/components/form-alert";
import { FormField } from "@/components/form-field";
import { ImageUploadField } from "@/components/image-upload";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { User } from "@/lib/api-types";
import { applyServerErrors, describeError } from "@/lib/form-errors";
import { KEEP_IMAGE, type ImageChoice } from "@/lib/image-choice";
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH } from "@/lib/validation";

function SuccessMessage({ message }: { message: string | null }) {
  // Always rendered so screen readers announce the text when it appears.
  return (
    <p role="status" className="text-sm text-muted-foreground">
      {message}
    </p>
  );
}

const profileSchema = z.object({
  full_name: z
    .string()
    .trim()
    .min(1, { error: "Enter your name." })
    .max(200, { error: "Use at most 200 characters." }),
});

type ProfileValues = z.infer<typeof profileSchema>;

function ProfileCard({ user }: { user: User }) {
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    setError,
    reset,
    formState: { errors, isSubmitting, isDirty },
  } = useForm<ProfileValues>({
    resolver: zodResolver(profileSchema),
    defaultValues: { full_name: user.full_name },
  });

  const onSubmit = handleSubmit(async (values) => {
    setFormError(null);
    setSaved(null);
    try {
      const updated = await updateProfile(values);
      queryClient.setQueryData(meQueryKey, updated);
      reset({ full_name: updated.full_name });
      setSaved("Your name has been saved.");
    } catch (error) {
      setFormError(applyServerErrors(error, setError, ["full_name"]));
    }
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Profile</h2>
        </CardTitle>
        <CardDescription>How your name appears to lab admins.</CardDescription>
      </CardHeader>
      <CardContent>
        {/* Stacked on narrow screens so a long email gets the full width. */}
        <dl className="mb-6 grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-[auto_1fr] sm:gap-y-2">
          <dt className="text-muted-foreground">Email</dt>
          <dd className="mb-2 [overflow-wrap:anywhere] sm:mb-0">{user.email}</dd>
          <dt className="text-muted-foreground">Role</dt>
          <dd className="mb-2 capitalize sm:mb-0">{user.role}</dd>
          <dt className="text-muted-foreground">Weekly quota</dt>
          <dd>{user.weekly_gpu_hour_quota} GPU-hours</dd>
        </dl>
        <form noValidate onSubmit={(event) => void onSubmit(event)} className="grid gap-4">
          <FormAlert message={formError} />
          <FormField
            label="Full name"
            autoComplete="name"
            error={errors.full_name?.message}
            {...register("full_name")}
          />
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" disabled={isSubmitting || !isDirty}>
              {isSubmitting ? "Saving…" : "Save name"}
            </Button>
            <SuccessMessage message={saved} />
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function AvatarCard({ user }: { user: User }) {
  const queryClient = useQueryClient();
  const [choice, setChoice] = useState<ImageChoice>(KEEP_IMAGE);
  const [uploading, setUploading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  // Bumped after each save so the field starts fresh and frees its local previews.
  const [fieldVersion, setFieldVersion] = useState(0);

  const changed = choice.kind === "set" || (choice.kind === "remove" && user.avatar_url !== null);

  const save = async () => {
    if (!changed) return;
    setFormError(null);
    setSaved(null);
    setSaving(true);
    try {
      const updated = await updateProfile({
        avatar_key: choice.kind === "set" ? choice.key : null,
      });
      queryClient.setQueryData(meQueryKey, updated);
      setChoice(KEEP_IMAGE);
      setFieldVersion((version) => version + 1);
      setSaved(
        updated.avatar_url === null ? "Your photo has been removed." : "Your photo has been saved.",
      );
    } catch (error) {
      setFormError(describeError(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Photo</h2>
        </CardTitle>
        <CardDescription>Shown next to your name. Up to 2 MiB.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <FormAlert message={formError} />
        <ImageUploadField
          key={fieldVersion}
          label="Profile photo"
          purpose="avatar"
          shape="round"
          currentUrl={user.avatar_url}
          alt="Your profile photo"
          choice={choice}
          onChoiceChange={(next) => {
            setSaved(null);
            setFormError(null);
            setChoice(next);
          }}
          onUploadingChange={setUploading}
        />
        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            disabled={saving || uploading || !changed}
            onClick={() => void save()}
          >
            {saving ? "Saving…" : "Save photo"}
          </Button>
          <SuccessMessage message={saved} />
        </div>
      </CardContent>
    </Card>
  );
}

const passwordSchema = z
  .object({
    current_password: z.string().min(1, { error: "Enter your current password." }),
    new_password: z
      .string()
      .min(PASSWORD_MIN_LENGTH, {
        error: `Use at least ${String(PASSWORD_MIN_LENGTH)} characters.`,
      })
      .max(PASSWORD_MAX_LENGTH, {
        error: `Use at most ${String(PASSWORD_MAX_LENGTH)} characters.`,
      }),
    confirm_password: z.string(),
  })
  .refine((values) => values.new_password === values.confirm_password, {
    error: "The passwords do not match.",
    path: ["confirm_password"],
  })
  .refine((values) => values.new_password !== values.current_password, {
    error: "Choose a password different from the current one.",
    path: ["new_password"],
  });

type PasswordValues = z.infer<typeof passwordSchema>;

const emptyPasswords: PasswordValues = {
  current_password: "",
  new_password: "",
  confirm_password: "",
};

function PasswordCard() {
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    setError,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<PasswordValues>({
    resolver: zodResolver(passwordSchema),
    defaultValues: emptyPasswords,
  });

  const onSubmit = handleSubmit(async ({ current_password, new_password }) => {
    setFormError(null);
    setSaved(null);
    try {
      const user = await changePassword({ current_password, new_password });
      queryClient.setQueryData(meQueryKey, user);
      reset(emptyPasswords);
      setSaved("Password changed. You have been signed out on other devices.");
    } catch (error) {
      setFormError(applyServerErrors(error, setError, ["current_password", "new_password"]));
    }
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Password</h2>
        </CardTitle>
        <CardDescription>Changing it signs you out everywhere else.</CardDescription>
      </CardHeader>
      <CardContent>
        <form noValidate onSubmit={(event) => void onSubmit(event)} className="grid gap-4">
          <FormAlert message={formError} />
          <FormField
            label="Current password"
            type="password"
            autoComplete="current-password"
            error={errors.current_password?.message}
            {...register("current_password")}
          />
          <FormField
            label="New password"
            type="password"
            autoComplete="new-password"
            description={`At least ${String(PASSWORD_MIN_LENGTH)} characters.`}
            error={errors.new_password?.message}
            {...register("new_password")}
          />
          <FormField
            label="Confirm new password"
            type="password"
            autoComplete="new-password"
            error={errors.confirm_password?.message}
            {...register("confirm_password")}
          />
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Changing…" : "Change password"}
            </Button>
            <SuccessMessage message={saved} />
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

export function AccountPage() {
  const { user } = useAuth();
  const demo = useDemoLogin();
  // RequireAuth guarantees a user here; this only satisfies the type.
  if (!user) return null;
  const isDemo = demo.data?.email === user.email;

  return (
    <>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Account</h1>
        <p className="mt-1 text-sm text-muted-foreground">Your profile, photo, and password.</p>
      </div>
      <div className="grid max-w-xl gap-6">
        {isDemo ? (
          <Card>
            <CardHeader>
              <CardTitle>
                <h2>Demo account</h2>
              </CardTitle>
              <CardDescription>
                You are signed in as {user.full_name} ({user.email}), the demo student shared with
                every visitor. Its name, photo, and password cannot be changed. Register your own
                account to try those.
              </CardDescription>
            </CardHeader>
          </Card>
        ) : (
          <>
            <ProfileCard user={user} />
            <AvatarCard user={user} />
            <PasswordCard />
          </>
        )}
      </div>
    </>
  );
}
