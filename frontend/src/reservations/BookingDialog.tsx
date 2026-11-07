import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";

import { FormAlert } from "@/components/form-alert";
import { FormField, SelectField, TextareaField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { Reservation, Server } from "@/lib/api-types";

/** What the dialog needs to know about a server: a full server, or a search result. */
export type BookableServer = Pick<
  Server,
  "id" | "hostname" | "gpu_count" | "max_booking_hours" | "requires_approval"
> & { lab: { name: string; timezone: string } };

/** Values to start the form with. Without an end, the booking lasts an hour. */
export interface BookingPrefill {
  start: Date;
  end?: Date;
  gpuCount?: number;
}
import { formatInLab, labDateKey, labLocalToUtc, labTimeOfDay } from "@/lib/lab-time";
import {
  ambiguityNote,
  HALF_HOURS,
  impliedEndDate,
  NONEXISTENT_TIME,
  placeBookingError,
} from "@/reservations/booking";
import {
  createReservation,
  getQuota,
  PURPOSE_MAX_LENGTH,
  quotaAtKey,
  reservationsKey,
} from "@/reservations/reservations-api";

const DEFAULT_LENGTH_MS = 60 * 60_000;

function makeSchema(timeZone: string, gpuCount: number) {
  return z
    .object({
      start_date: z.string().min(1, { error: "Pick a start date." }),
      start_time: z.string().min(1, { error: "Pick a start time." }),
      end_date: z.string().min(1, { error: "Pick an end date." }),
      end_time: z.string().min(1, { error: "Pick an end time." }),
      gpu_count: z
        .number({ error: "Enter how many GPUs you need." })
        .int({ error: "Enter a whole number." })
        .min(1, { error: "Book at least one GPU." })
        .max(gpuCount, { error: `This server has ${String(gpuCount)} GPUs.` }),
      purpose: z
        .string()
        .trim()
        .min(1, { error: "Say what the GPUs are for." })
        .max(PURPOSE_MAX_LENGTH, {
          error: `Use at most ${String(PURPOSE_MAX_LENGTH)} characters.`,
        }),
    })
    .superRefine((values, ctx) => {
      const start = labLocalToUtc(values.start_date, values.start_time, timeZone);
      const end = labLocalToUtc(values.end_date, values.end_time, timeZone);
      if (!start.ok) {
        ctx.addIssue({
          code: "custom",
          path: ["start_time"],
          message: start.error === "nonexistent" ? NONEXISTENT_TIME : "Pick a valid start.",
        });
      }
      if (!end.ok) {
        ctx.addIssue({
          code: "custom",
          path: ["end_time"],
          message: end.error === "nonexistent" ? NONEXISTENT_TIME : "Pick a valid end.",
        });
      }
      if (start.ok && end.ok && end.utc <= start.utc) {
        ctx.addIssue({ code: "custom", path: ["end_time"], message: "End after the start." });
      }
    });
}

type BookingValues = z.infer<ReturnType<typeof makeSchema>>;

/**
 * Quota usage in the week the chosen start falls in. Next week's bookings are checked
 * against next week, so the current week's numbers would mislead here.
 */
function WeekQuota({ start }: { start: Date | null }) {
  const at = start?.toISOString() ?? null;
  const quota = useQuery({
    queryKey: quotaAtKey(at ?? ""),
    queryFn: ({ signal }) => getQuota(signal, start ?? undefined),
    enabled: at !== null,
  });
  if (at === null) return null;
  let text: string;
  if (quota.isPending) text = "Checking your GPU-hours for that week…";
  else if (quota.isError) text = "Could not load your GPU-hours for that week.";
  else {
    const { used_hours: used, quota_hours: total, remaining_hours: left } = quota.data;
    const week = formatInLab(quota.data.week_start, quota.data.timezone);
    text = `Week starting ${week}: ${String(used)} of ${String(total)} GPU-hours used, ${String(left)} left.`;
  }
  return (
    <p className="text-xs text-muted-foreground" aria-live="polite">
      {text}
    </p>
  );
}

function defaults(server: BookableServer, prefill: BookingPrefill): BookingValues {
  const zone = server.lab.timezone;
  const { start } = prefill;
  const end = prefill.end ?? new Date(start.getTime() + DEFAULT_LENGTH_MS);
  return {
    start_date: labDateKey(start, zone),
    start_time: labTimeOfDay(start, zone),
    end_date: labDateKey(end, zone),
    end_time: labTimeOfDay(end, zone),
    gpu_count: prefill.gpuCount ?? 1,
    purpose: "",
  };
}

function BookingForm({
  server,
  prefill,
  onBooked,
}: {
  server: BookableServer;
  prefill: BookingPrefill;
  onBooked: (reservation: Reservation) => void;
}) {
  const zone = server.lab.timezone;
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  // Whether the end date was chosen rather than implied (a multi-day booking). A
  // prefilled end, such as a search result's, counts as chosen.
  const [endDatePicked, setEndDatePicked] = useState(prefill.end !== undefined);
  const schema = useMemo(() => makeSchema(zone, server.gpu_count), [zone, server.gpu_count]);
  const {
    register,
    handleSubmit,
    setError,
    setValue,
    getValues,
    control,
    formState: { errors, isSubmitting },
  } = useForm<BookingValues>({
    resolver: zodResolver(schema),
    defaultValues: defaults(server, prefill),
  });

  /** Keep the end date consistent with the start and end times; see impliedEndDate. */
  function syncEndDate(picked: boolean) {
    const values = getValues();
    const endDate = impliedEndDate({
      startDate: values.start_date,
      startTime: values.start_time,
      endDate: values.end_date,
      endTime: values.end_time,
      endDatePicked: picked,
    });
    if (endDate !== values.end_date) setValue("end_date", endDate);
  }

  const [startDate, startTime, endDate, endTime] = useWatch({
    control,
    name: ["start_date", "start_time", "end_date", "end_time"],
  });

  const plannedStart = useMemo(() => {
    const result = labLocalToUtc(startDate, startTime, zone);
    return result.ok ? result.utc : null;
  }, [startDate, startTime, zone]);

  const submit = handleSubmit(async (values) => {
    setFormError(null);
    const startUtc = labLocalToUtc(values.start_date, values.start_time, zone);
    const endUtc = labLocalToUtc(values.end_date, values.end_time, zone);
    if (!startUtc.ok || !endUtc.ok) return; // the schema already reported it
    try {
      const reservation = await createReservation({
        server_id: server.id,
        gpu_count: values.gpu_count,
        starts_at: startUtc.utc.toISOString(),
        ends_at: endUtc.utc.toISOString(),
        purpose: values.purpose,
      });
      // Availability, lists, and the quota all live under this key.
      await queryClient.invalidateQueries({ queryKey: reservationsKey });
      onBooked(reservation);
    } catch (error) {
      const placed = placeBookingError(error);
      for (const { field, message } of placed.fields) {
        setError(field, { type: "server", message });
      }
      setFormError(placed.form);
    }
  });

  const keepEndDate = () => {
    syncEndDate(endDatePicked);
  };
  const startField = register("start_time", { onChange: keepEndDate });
  const endField = register("end_time", { onChange: keepEndDate });

  return (
    <form noValidate onSubmit={(event) => void submit(event)} className="grid gap-4">
      <FormAlert message={formError} />
      <div className="grid grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)] gap-3">
        <FormField
          label="Start date"
          type="date"
          error={errors.start_date?.message}
          {...register("start_date", { onChange: keepEndDate })}
        />
        <SelectField
          label="Start time"
          error={errors.start_time?.message}
          description={ambiguityNote(startDate, startTime, zone)}
          {...startField}
        >
          {HALF_HOURS.map((time) => (
            <option key={time} value={time}>
              {time}
            </option>
          ))}
        </SelectField>
        <FormField
          label="End date"
          type="date"
          error={errors.end_date?.message}
          {...register("end_date", {
            onChange: () => {
              setEndDatePicked(true);
              syncEndDate(true);
            },
          })}
        />
        <SelectField
          label="End time"
          error={errors.end_time?.message}
          description={ambiguityNote(endDate, endTime, zone)}
          {...endField}
        >
          {HALF_HOURS.map((time) => (
            <option key={time} value={time}>
              {time}
            </option>
          ))}
        </SelectField>
      </div>
      <p className="text-xs text-muted-foreground">
        Times are in {zone}. Bookings last up to {server.max_booking_hours} hours. On weekdays from
        09:00 to 18:00, one booking can use at most 4 GPU-hours.
      </p>
      <FormField
        label="GPUs"
        type="number"
        inputMode="numeric"
        min={1}
        max={server.gpu_count}
        error={errors.gpu_count?.message}
        {...register("gpu_count", { valueAsNumber: true })}
      />
      <WeekQuota start={plannedStart} />
      <TextareaField
        label="Purpose"
        rows={3}
        placeholder="Fine-tuning a 7B model with LoRA"
        error={errors.purpose?.message}
        {...register("purpose")}
      />
      {server.requires_approval && (
        <p className="text-sm text-muted-foreground">
          An admin approves bookings on this server. It holds the GPUs while it waits.
        </p>
      )}
      <DialogFooter>
        <Button type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Booking…" : "Book"}
        </Button>
      </DialogFooter>
    </form>
  );
}

export function BookingDialog({
  server,
  start,
  end,
  gpuCount,
  onOpenChange,
  onBooked,
}: {
  server: BookableServer;
  /** The start to prefill, or null when the dialog is closed. */
  start: Date | null;
  /** The end to prefill; an hour after the start when omitted. */
  end?: Date;
  gpuCount?: number;
  onOpenChange: (open: boolean) => void;
  onBooked: (reservation: Reservation) => void;
}) {
  return (
    <Dialog open={start !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Book {server.hostname}</DialogTitle>
          <DialogDescription>
            Pick a time in {server.lab.name}&apos;s time zone and how many GPUs you need.
          </DialogDescription>
        </DialogHeader>
        {/* Content unmounts when closed, so every opening starts fresh. */}
        {start && (
          <BookingForm
            server={server}
            prefill={{ start, ...(end && { end }), ...(gpuCount && { gpuCount }) }}
            onBooked={onBooked}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
