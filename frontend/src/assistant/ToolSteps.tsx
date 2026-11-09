import { CircleCheck, CircleX, LoaderCircle } from "lucide-react";

export interface LiveStep {
  name: string;
  label: string;
  /** Undefined while the tool is running. */
  ok?: boolean;
}

export function ToolSteps({ steps }: { steps: LiveStep[] }) {
  if (steps.length === 0) return null;
  return (
    <ul aria-label="Assistant steps" className="space-y-1 text-xs text-muted-foreground">
      {steps.map((step, index) => (
        <li key={`${step.name}-${String(index)}`} className="flex items-center gap-1.5">
          {step.ok === undefined ? (
            <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />
          ) : step.ok ? (
            <CircleCheck className="size-3.5 text-emerald-600" aria-hidden="true" />
          ) : (
            <CircleX className="size-3.5 text-destructive" aria-hidden="true" />
          )}
          <span>
            {step.label}
            {step.ok === false && " (failed)"}
          </span>
        </li>
      ))}
    </ul>
  );
}
