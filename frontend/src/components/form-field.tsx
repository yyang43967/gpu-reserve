import { useId, type ComponentProps, type ReactNode } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";

interface FieldProps {
  label: string;
  error?: string | undefined;
  description?: string;
}

interface ControlAria {
  id: string;
  "aria-invalid": true | undefined;
  "aria-describedby": string | undefined;
}

/** Label, description and error around one control, wired up for screen readers. */
function Field({
  label,
  error,
  description,
  id,
  children,
}: FieldProps & { id: string | undefined; children: (aria: ControlAria) => ReactNode }) {
  const generatedId = useId();
  const controlId = id ?? generatedId;
  const errorId = `${controlId}-error`;
  const descriptionId = `${controlId}-description`;
  const describedBy = [description && !error ? descriptionId : null, error ? errorId : null]
    .filter(Boolean)
    .join(" ");

  return (
    <div className="grid gap-2">
      <Label htmlFor={controlId}>{label}</Label>
      {children({
        id: controlId,
        "aria-invalid": error ? true : undefined,
        "aria-describedby": describedBy || undefined,
      })}
      {description && !error && (
        <p id={descriptionId} className="text-xs text-muted-foreground">
          {description}
        </p>
      )}
      {error && (
        <p id={errorId} className="text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

type FormFieldProps = ComponentProps<typeof Input> & FieldProps;

/** A labelled input with its error announced to screen readers. */
export function FormField({ label, error, description, id, ...inputProps }: FormFieldProps) {
  return (
    <Field label={label} error={error} description={description} id={id}>
      {(aria) => <Input {...aria} {...inputProps} />}
    </Field>
  );
}

type SelectFieldProps = ComponentProps<typeof NativeSelect> & FieldProps;

/** A labelled native select with the same error handling as FormField. */
export function SelectField({ label, error, description, id, ...selectProps }: SelectFieldProps) {
  return (
    <Field label={label} error={error} description={description} id={id}>
      {(aria) => <NativeSelect {...aria} {...selectProps} />}
    </Field>
  );
}

type TextareaFieldProps = ComponentProps<typeof Textarea> & FieldProps;

/** A labelled multi-line input with the same error handling as FormField. */
export function TextareaField({
  label,
  error,
  description,
  id,
  ...textareaProps
}: TextareaFieldProps) {
  return (
    <Field label={label} error={error} description={description} id={id}>
      {(aria) => <Textarea {...aria} {...textareaProps} />}
    </Field>
  );
}
