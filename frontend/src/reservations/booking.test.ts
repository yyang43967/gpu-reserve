import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import {
  ambiguityNote,
  impliedEndDate,
  nextBookableStart,
  placeBookingError,
} from "@/reservations/booking";

describe("impliedEndDate", () => {
  const base = { startDate: "2030-01-11", endDate: "2030-01-11", endDatePicked: false };

  it("follows the start date for a same-day booking", () => {
    expect(impliedEndDate({ ...base, startTime: "09:00", endTime: "11:00" })).toBe("2030-01-11");
  });

  it("moves to the next day for an overnight booking", () => {
    expect(impliedEndDate({ ...base, startTime: "22:00", endTime: "02:00" })).toBe("2030-01-12");
  });

  it("treats an end at 00:00 as midnight at the end of the start day", () => {
    expect(impliedEndDate({ ...base, startTime: "22:00", endTime: "00:00" })).toBe("2030-01-12");
  });

  it("moves back when the end time is moved after the start again", () => {
    expect(
      impliedEndDate({ ...base, endDate: "2030-01-12", startTime: "22:00", endTime: "23:00" }),
    ).toBe("2030-01-11");
  });

  it("keeps an end date the person picked for a multi-day booking", () => {
    expect(
      impliedEndDate({
        ...base,
        endDate: "2030-01-13",
        endDatePicked: true,
        startTime: "22:00",
        endTime: "06:00",
      }),
    ).toBe("2030-01-13");
  });

  it("still advances a picked end date that equals the start date", () => {
    expect(
      impliedEndDate({ ...base, endDatePicked: true, startTime: "22:00", endTime: "01:00" }),
    ).toBe("2030-01-12");
  });

  it("follows a changed start date until the end date is picked", () => {
    expect(
      impliedEndDate({
        ...base,
        startDate: "2030-01-20",
        startTime: "09:00",
        endTime: "10:00",
      }),
    ).toBe("2030-01-20");
  });
});

describe("helpers", () => {
  it("finds the next half hour", () => {
    expect(nextBookableStart(new Date("2030-01-12T16:10:00Z")).toISOString()).toBe(
      "2030-01-12T16:30:00.000Z",
    );
    expect(nextBookableStart(new Date("2030-01-12T16:30:00Z")).toISOString()).toBe(
      "2030-01-12T17:00:00.000Z",
    );
  });

  it("notes ambiguous fall-back times only", () => {
    expect(ambiguityNote("2030-11-03", "01:30", "America/Los_Angeles")).toBe(
      "01:30 happens twice that day; this is the first one (PDT).",
    );
    expect(ambiguityNote("2030-11-03", "03:00", "America/Los_Angeles")).toBeUndefined();
  });
});

function apiError(status: number, slug: string, detail?: string, errors = []): ApiError {
  return new ApiError(status, {
    type: `https://gpureserve.dev/errors/${slug}`,
    title: slug,
    status,
    detail,
    errors,
  });
}

describe("placeBookingError", () => {
  it("puts known problem types under their field with the API's detail", () => {
    expect(
      placeBookingError(apiError(409, "gpu-unavailable", "Only 1 of 2 requested GPUs is free.")),
    ).toEqual({
      fields: [{ field: "gpu_count", message: "Only 1 of 2 requested GPUs is free." }],
      form: null,
    });
    expect(
      placeBookingError(apiError(422, "booking-too-long", "At most 12 hours.")).fields,
    ).toEqual([{ field: "end_time", message: "At most 12 hours." }]);
  });

  it("shows form-level types and unknown types above the form with the detail", () => {
    expect(placeBookingError(apiError(409, "quota-exceeded", "Week is full."))).toEqual({
      fields: [],
      form: "Week is full.",
    });
    expect(placeBookingError(apiError(409, "something-new", "A new rule said no."))).toEqual({
      fields: [],
      form: "A new rule said no.",
    });
  });

  it("maps validation errors on request fields to form fields", () => {
    const error = new ApiError(422, {
      type: "https://gpureserve.dev/errors/validation-error",
      title: "Validation error",
      status: 422,
      errors: [
        { field: "starts_at", message: "needs an offset" },
        { field: "server_id", message: "is not a UUID" },
      ],
    });
    expect(placeBookingError(error)).toEqual({
      fields: [{ field: "start_time", message: "needs an offset" }],
      form: "is not a UUID",
    });
  });

  it("describes network failures", () => {
    expect(placeBookingError(new TypeError("Failed to fetch")).form).toMatch(/Could not reach/);
  });
});
