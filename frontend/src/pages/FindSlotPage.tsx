import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { Link } from "react-router";
import { z } from "zod";

import { FormField, SelectField } from "@/components/form-field";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import type { GpuModel, Slot } from "@/lib/api-types";
import { describeError } from "@/lib/form-errors";
import {
  ambiguityNote,
  HALF_HOURS,
  NONEXISTENT_TIME,
  nextBookableStart,
} from "@/reservations/booking";
import {
  formatInLab,
  formatLabRange,
  labDateKey,
  labLocalToUtc,
  labTimeOfDay,
} from "@/lib/lab-time";
import { DEFAULT_LAB_TIMEZONE, labsListKey, listLabs } from "@/labs/labs-api";
import { BookingDialog, type BookableServer } from "@/reservations/BookingDialog";
import { reservationsKey } from "@/reservations/reservations-api";
import {
  initialZone,
  MAX_SEARCH_DAYS,
  searchSlots,
  type SlotSearchInput,
} from "@/search/search-api";
import { GPU_MODELS, MAX_GPUS_PER_SERVER } from "@/servers/servers-api";

const DAY_MS = 24 * 60 * 60_000;
const DEFAULT_WINDOW_DAYS = 7;
const MAX_DURATION_HOURS = 168;
const GPU_MODEL_KEYS = Object.keys(GPU_MODELS) as GpuModel[];
const MEMORY_OPTIONS = [...new Set(Object.values(GPU_MODELS).map((m) => m.memoryGb))].sort(
  (a, b) => a - b,
);
const LAB_QUERY = { page: 1, isActive: true } as const;

function makeSchema() {
  return z
    .object({
      gpu_count: z
        .number({ error: "Enter how many GPUs you need." })
        .int({ error: "Enter a whole number." })
        .min(1, { error: "Ask for at least one GPU." })
        .max(MAX_GPUS_PER_SERVER, {
          error: `No server has more than ${String(MAX_GPUS_PER_SERVER)} GPUs.`,
        }),
      duration_hours: z
        .number({ error: "Enter how long the job runs." })
        .min(0.5, { error: "Jobs last at least half an hour." })
        .max(MAX_DURATION_HOURS, { error: `At most ${String(MAX_DURATION_HOURS)} hours.` })
        .refine((hours) => Number.isInteger(hours * 2), {
          error: "Use whole or half hours, such as 2 or 2.5.",
        }),
      gpu_model: z.string(),
      min_memory: z.string(),
      time_zone: z.string().min(1),
      from_date: z.string().min(1, { error: "Pick a date." }),
      from_time: z.string().min(1, { error: "Pick a time." }),
      to_date: z.string().min(1, { error: "Pick a date." }),
      to_time: z.string().min(1, { error: "Pick a time." }),
    })
    .superRefine((values, ctx) => {
      const from = labLocalToUtc(values.from_date, values.from_time, values.time_zone);
      const to = labLocalToUtc(values.to_date, values.to_time, values.time_zone);
      if (!from.ok) {
        ctx.addIssue({
          code: "custom",
          path: ["from_time"],
          message: from.error === "nonexistent" ? NONEXISTENT_TIME : "Pick a valid time.",
        });
      }
      if (!to.ok) {
        ctx.addIssue({
          code: "custom",
          path: ["to_time"],
          message: to.error === "nonexistent" ? NONEXISTENT_TIME : "Pick a valid time.",
        });
      }
      if (!from.ok || !to.ok) return;
      const span = to.utc.getTime() - from.utc.getTime();
      if (span < values.duration_hours * 60 * 60_000) {
        ctx.addIssue({
          code: "custom",
          path: ["to_time"],
          message: "The window must be at least as long as the job.",
        });
      } else if (span > MAX_SEARCH_DAYS * DAY_MS) {
        ctx.addIssue({
          code: "custom",
          path: ["to_date"],
          message: `Search at most ${String(MAX_SEARCH_DAYS)} days at once.`,
        });
      }
    });
}

const searchSchema = makeSchema();
type SearchValues = z.infer<typeof searchSchema>;

function defaultValues(timeZone: string, now: Date): SearchValues {
  const from = nextBookableStart(now);
  const to = new Date(from.getTime() + DEFAULT_WINDOW_DAYS * DAY_MS);
  return {
    gpu_count: 1,
    duration_hours: 2,
    gpu_model: "",
    min_memory: "",
    time_zone: timeZone,
    from_date: labDateKey(from, timeZone),
    from_time: labTimeOfDay(from, timeZone),
    to_date: labDateKey(to, timeZone),
    to_time: labTimeOfDay(to, timeZone),
  };
}

function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

function toInput(values: SearchValues): SlotSearchInput | null {
  const from = labLocalToUtc(values.from_date, values.from_time, values.time_zone);
  const to = labLocalToUtc(values.to_date, values.to_time, values.time_zone);
  if (!from.ok || !to.ok) return null;
  return {
    gpu_count: values.gpu_count,
    duration_minutes: Math.round(values.duration_hours * 60),
    earliest: from.utc.toISOString(),
    latest: to.utc.toISOString(),
    gpu_model: GPU_MODEL_KEYS.find((m) => m === values.gpu_model) ?? null,
    min_gpu_memory_gb: values.min_memory ? Number(values.min_memory) : null,
  };
}

function bookable(slot: Slot): BookableServer {
  return {
    id: slot.server.id,
    hostname: slot.server.hostname,
    gpu_count: slot.server.gpu_count,
    max_booking_hours: slot.server.max_booking_hours,
    requires_approval: slot.server.requires_approval,
    lab: { name: slot.lab.name, timezone: slot.lab.timezone },
  };
}

function SearchForm({
  timeZones,
  onSearch,
}: {
  timeZones: string[];
  onSearch: (input: SlotSearchInput) => void;
}) {
  const {
    register,
    handleSubmit,
    control,
    formState: { errors },
  } = useForm<SearchValues>({
    resolver: zodResolver(searchSchema),
    defaultValues: defaultValues(initialZone(timeZones, browserTimeZone()), new Date()),
  });
  const [zone, fromDate, fromTime, toDate, toTime] = useWatch({
    control,
    name: ["time_zone", "from_date", "from_time", "to_date", "to_time"],
  });

  const submit = handleSubmit((values) => {
    const input = toInput(values);
    if (input) onSearch(input);
  });

  return (
    <form
      noValidate
      aria-label="Search for a slot"
      onSubmit={(event) => void submit(event)}
      className="mb-6 grid gap-4 rounded-xl border p-4"
    >
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <FormField
          label="GPUs"
          type="number"
          inputMode="numeric"
          min={1}
          max={MAX_GPUS_PER_SERVER}
          error={errors.gpu_count?.message}
          {...register("gpu_count", { valueAsNumber: true })}
        />
        <FormField
          label="Hours"
          type="number"
          inputMode="decimal"
          min={0.5}
          step={0.5}
          description="Whole or half hours."
          error={errors.duration_hours?.message}
          {...register("duration_hours", { valueAsNumber: true })}
        />
        <SelectField label="GPU model" {...register("gpu_model")}>
          <option value="">Any model</option>
          {GPU_MODEL_KEYS.map((model) => (
            <option key={model} value={model}>
              {GPU_MODELS[model].label}
            </option>
          ))}
        </SelectField>
        <SelectField label="Memory per GPU" {...register("min_memory")}>
          <option value="">Any</option>
          {MEMORY_OPTIONS.map((gb) => (
            <option key={gb} value={String(gb)}>
              At least {gb} GB
            </option>
          ))}
        </SelectField>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <FormField
          label="From date"
          type="date"
          error={errors.from_date?.message}
          {...register("from_date")}
        />
        <SelectField
          label="From time"
          error={errors.from_time?.message}
          description={ambiguityNote(fromDate, fromTime, zone)}
          {...register("from_time")}
        >
          {HALF_HOURS.map((time) => (
            <option key={time} value={time}>
              {time}
            </option>
          ))}
        </SelectField>
        <FormField
          label="To date"
          type="date"
          error={errors.to_date?.message}
          {...register("to_date")}
        />
        <SelectField
          label="To time"
          error={errors.to_time?.message}
          description={ambiguityNote(toDate, toTime, zone)}
          {...register("to_time")}
        >
          {HALF_HOURS.map((time) => (
            <option key={time} value={time}>
              {time}
            </option>
          ))}
        </SelectField>
        <SelectField
          label="Time zone"
          description="The dates and times above are in this zone."
          {...register("time_zone")}
        >
          {timeZones.map((tz) => (
            <option key={tz} value={tz}>
              {tz}
            </option>
          ))}
        </SelectField>
      </div>
      <p className="text-xs text-muted-foreground">
        Results start no earlier than “From” and end by “To”, follow the booking rules (peak hours,
        server limits, your weekly quota), and are shown in each lab&apos;s time zone.
      </p>
      <div>
        <Button type="submit">Search</Button>
      </div>
    </form>
  );
}

function SlotItem({ slot, onBook }: { slot: Slot; onBook: () => void }) {
  const zone = slot.lab.timezone;
  const model = GPU_MODELS[slot.server.gpu_model].label;
  return (
    <li
      className="grid gap-2 rounded-xl border p-4 sm:grid-cols-[1fr_auto] sm:items-center"
      aria-label={`${slot.server.hostname}, ${formatInLab(slot.starts_at, zone)}`}
    >
      <div className="grid gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <Link
            to={`/servers/${slot.server.id}`}
            className="font-medium underline-offset-4 hover:underline"
          >
            {slot.server.hostname}
          </Link>
          {slot.server.requires_approval && <Badge variant="secondary">Needs approval</Badge>}
        </div>
        <p className="text-sm tabular-nums">{formatLabRange(slot.starts_at, slot.ends_at, zone)}</p>
        <p className="text-sm text-muted-foreground">
          {slot.lab.name} · {model}, {slot.server.gpu_memory_gb} GB · {slot.free_gpus} of{" "}
          {slot.server.gpu_count} GPUs free
        </p>
      </div>
      <div>
        <Button size="sm" onClick={onBook}>
          Book
        </Button>
      </div>
    </li>
  );
}

function Results({ input, onBook }: { input: SlotSearchInput; onBook: (slot: Slot) => void }) {
  const results = useQuery({
    // Under the reservations key: booking a slot invalidates it, so the list refreshes.
    queryKey: [...reservationsKey, "search", input],
    queryFn: () => searchSlots(input),
  });

  if (results.isPending) return <LoadingState label="Searching" />;
  if (results.isError) {
    const error = results.error;
    const known = error instanceof ApiError && (error.status === 409 || error.status === 422);
    return (
      <ErrorState
        title={known ? "No search possible" : "Search failed"}
        message={describeError(error)}
        {...(!known && {
          onRetry: () => {
            void results.refetch();
          },
        })}
      />
    );
  }
  if (results.data.items.length === 0) {
    return (
      <EmptyState
        title="Nothing fits"
        description="Try a wider window, fewer GPUs, or a shorter job. Your weekly quota and peak-hour limits apply."
      />
    );
  }
  return (
    <ul aria-label="Matching slots" aria-busy={results.isFetching} className="grid gap-3">
      {results.data.items.map((slot) => (
        <SlotItem
          key={`${slot.server.id}-${slot.starts_at}`}
          slot={slot}
          onBook={() => {
            onBook(slot);
          }}
        />
      ))}
    </ul>
  );
}

export function FindSlotPage() {
  const labs = useQuery({
    queryKey: labsListKey(LAB_QUERY),
    queryFn: ({ signal }) => listLabs(LAB_QUERY, signal),
  });
  const [search, setSearch] = useState<SlotSearchInput | null>(null);
  const [booking, setBooking] = useState<Slot | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  let form;
  if (labs.isPending) {
    form = <LoadingState label="Loading labs" />;
  } else if (labs.isError) {
    form = (
      <ErrorState
        title="Could not load labs"
        message={labs.error.message}
        onRetry={() => {
          void labs.refetch();
        }}
      />
    );
  } else {
    const zones = [...new Set(labs.data.items.map((lab) => lab.timezone))];
    form = (
      <SearchForm
        timeZones={zones.length ? zones : [DEFAULT_LAB_TIMEZONE]}
        onSearch={(input) => {
          setNotice(null);
          setSearch(input);
        }}
      />
    );
  }

  return (
    <>
      <PageHeader
        title="Find a slot"
        description="Describe the job and a window; get the times that fit, best first."
      />
      {form}
      <p role="status" className="mb-3 text-sm empty:hidden">
        {notice}
        {notice && (
          <>
            {" "}
            <Link to="/reservations" className="underline underline-offset-4">
              See my reservations
            </Link>
          </>
        )}
      </p>
      {search && <Results input={search} onBook={setBooking} />}
      {booking && (
        <BookingDialog
          server={bookable(booking)}
          start={new Date(booking.starts_at)}
          end={new Date(booking.ends_at)}
          gpuCount={search?.gpu_count ?? 1}
          onOpenChange={(open) => {
            if (!open) setBooking(null);
          }}
          onBooked={(reservation) => {
            setBooking(null);
            const when = formatInLab(reservation.starts_at, reservation.lab.timezone);
            setNotice(
              reservation.status === "pending"
                ? `Requested ${reservation.server.hostname} from ${when}; an admin will approve it.`
                : `Booked ${reservation.server.hostname} from ${when}.`,
            );
          }}
        />
      )}
    </>
  );
}
