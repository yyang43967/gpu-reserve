import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useSearchParams } from "react-router";
import { z } from "zod";

import { useAuth } from "@/auth/auth-context";
import { useDemoLogin } from "@/auth/use-demo-login";
import { FormAlert } from "@/components/form-alert";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { applyServerErrors } from "@/lib/form-errors";

const loginSchema = z.object({
  email: z.email({ error: "Enter a valid email address." }),
  password: z.string().min(1, { error: "Enter your password." }),
});

type LoginValues = z.infer<typeof loginSchema>;

export function LoginPage() {
  const { login } = useAuth();
  const demo = useDemoLogin();
  const [searchParams] = useSearchParams();
  const [formError, setFormError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  });

  const onSubmit = handleSubmit(async (values) => {
    setFormError(null);
    try {
      // On success the route guard sends the user on to `next`.
      await login(values);
    } catch (error) {
      setFormError(applyServerErrors(error, setError, ["email", "password"]));
    }
  });

  const signInAsDemo = async () => {
    if (!demo.data) return;
    setFormError(null);
    try {
      await login(demo.data);
    } catch (error) {
      setFormError(applyServerErrors(error, setError, ["email", "password"]));
    }
  };

  const next = searchParams.get("next");
  const registerLink = next ? `/register?next=${encodeURIComponent(next)}` : "/register";

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h1>Sign in</h1>
        </CardTitle>
        <CardDescription>Reserve GPUs on your lab&apos;s servers.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-6">
        {demo.data && (
          <section
            aria-labelledby="demo-heading"
            className="grid gap-2 rounded-md border bg-muted/50 p-4 text-sm"
          >
            <h2 id="demo-heading" className="font-medium">
              Try the demo
            </h2>
            <p className="text-muted-foreground">
              Sign in as a demo student, shared with every visitor. Bookings and assistant messages
              work; the profile and password cannot be changed.
            </p>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
              <dt className="text-muted-foreground">Email</dt>
              <dd className="font-mono">{demo.data.email}</dd>
              <dt className="text-muted-foreground">Password</dt>
              <dd className="font-mono">{demo.data.password}</dd>
            </dl>
            <Button
              type="button"
              variant="secondary"
              disabled={isSubmitting}
              onClick={() => void signInAsDemo()}
            >
              Sign in as the demo student
            </Button>
          </section>
        )}
        <form noValidate onSubmit={(event) => void onSubmit(event)} className="grid gap-4">
          <FormAlert message={formError} />
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
            autoComplete="current-password"
            error={errors.password?.message}
            {...register("password")}
          />
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Signing in…" : "Sign in"}
          </Button>
          <p className="text-sm text-muted-foreground">
            No account?{" "}
            <Link to={registerLink} className="text-foreground underline underline-offset-4">
              Register
            </Link>
          </p>
        </form>
      </CardContent>
    </Card>
  );
}
