import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useSearchParams } from "react-router";
import { z } from "zod";

import { useAuth } from "@/auth/auth-context";
import { FormAlert } from "@/components/form-alert";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { applyServerErrors } from "@/lib/form-errors";
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH } from "@/lib/validation";

const registerSchema = z.object({
  full_name: z
    .string()
    .trim()
    .min(1, { error: "Enter your name." })
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
});

type RegisterValues = z.infer<typeof registerSchema>;

export function RegisterPage() {
  const { register: createAccount } = useAuth();
  const [searchParams] = useSearchParams();
  const [formError, setFormError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<RegisterValues>({
    resolver: zodResolver(registerSchema),
    defaultValues: { full_name: "", email: "", password: "" },
  });

  const onSubmit = handleSubmit(async (values) => {
    setFormError(null);
    try {
      await createAccount(values);
    } catch (error) {
      setFormError(applyServerErrors(error, setError, ["full_name", "email", "password"]));
    }
  });

  const next = searchParams.get("next");
  const loginLink = next ? `/login?next=${encodeURIComponent(next)}` : "/login";

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h1>Create an account</h1>
        </CardTitle>
        <CardDescription>Students can book GPUs within their weekly quota.</CardDescription>
      </CardHeader>
      <CardContent>
        <form noValidate onSubmit={(event) => void onSubmit(event)} className="grid gap-4">
          <FormAlert message={formError} />
          <FormField
            label="Full name"
            autoComplete="name"
            error={errors.full_name?.message}
            {...register("full_name")}
          />
          <FormField
            label="Email"
            type="email"
            autoComplete="email"
            error={errors.email?.message}
            {...register("email")}
          />
          <FormField
            label="Password"
            type="password"
            autoComplete="new-password"
            description={`At least ${String(PASSWORD_MIN_LENGTH)} characters.`}
            error={errors.password?.message}
            {...register("password")}
          />
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Creating account…" : "Create account"}
          </Button>
          <p className="text-sm text-muted-foreground">
            Already registered?{" "}
            <Link to={loginLink} className="text-foreground underline underline-offset-4">
              Sign in
            </Link>
          </p>
        </form>
      </CardContent>
    </Card>
  );
}
