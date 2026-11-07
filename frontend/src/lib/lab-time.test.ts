import { describe, expect, it } from "vitest";

import {
  formatInLab,
  hasHalfHourOffsets,
  labDateKey,
  labLocalToUtc,
  labTimeOfDay,
  offsetMinutes,
  zoneAbbreviation,
} from "@/lib/lab-time";

const LA = "America/Los_Angeles";
const TOKYO = "Asia/Tokyo";

function utc(iso: string): Date {
  return new Date(iso);
}

describe("labLocalToUtc in America/Los_Angeles", () => {
  it("converts an ordinary winter time", () => {
    expect(labLocalToUtc("2030-01-12", "08:00", LA)).toEqual({
      ok: true,
      utc: utc("2030-01-12T16:00:00Z"),
      ambiguous: false,
    });
  });

  it("converts an ordinary summer time", () => {
    expect(labLocalToUtc("2030-07-12", "08:00", LA)).toEqual({
      ok: true,
      utc: utc("2030-07-12T15:00:00Z"),
      ambiguous: false,
    });
  });

  describe("on the spring-forward day (10 March 2030)", () => {
    it("rejects a time in the skipped hour", () => {
      expect(labLocalToUtc("2030-03-10", "02:30", LA)).toEqual({
        ok: false,
        error: "nonexistent",
      });
      expect(labLocalToUtc("2030-03-10", "02:00", LA)).toEqual({
        ok: false,
        error: "nonexistent",
      });
    });

    it("accepts the times on either side of the gap", () => {
      expect(labLocalToUtc("2030-03-10", "01:30", LA)).toMatchObject({
        ok: true,
        utc: utc("2030-03-10T09:30:00Z"), // PST
      });
      expect(labLocalToUtc("2030-03-10", "03:00", LA)).toMatchObject({
        ok: true,
        utc: utc("2030-03-10T10:00:00Z"), // PDT
      });
    });
  });

  describe("on the fall-back day (3 November 2030)", () => {
    it("resolves a repeated time to its first occurrence and flags it", () => {
      const result = labLocalToUtc("2030-11-03", "01:30", LA);

      expect(result).toEqual({ ok: true, utc: utc("2030-11-03T08:30:00Z"), ambiguous: true });
      // The first 01:30 is still daylight time.
      if (result.ok) expect(zoneAbbreviation(result.utc, LA)).toBe("PDT");
    });

    it("shows the second occurrence with its own abbreviation", () => {
      const second = utc("2030-11-03T09:30:00Z");

      expect(labTimeOfDay(second, LA)).toBe("01:30");
      expect(zoneAbbreviation(second, LA)).toBe("PST");
    });

    it("is not ambiguous outside the repeated hour", () => {
      expect(labLocalToUtc("2030-11-03", "02:00", LA)).toEqual({
        ok: true,
        utc: utc("2030-11-03T10:00:00Z"),
        ambiguous: false,
      });
    });
  });

  it("rejects malformed and impossible input", () => {
    expect(labLocalToUtc("2030-02-30", "08:00", LA)).toEqual({ ok: false, error: "invalid" });
    expect(labLocalToUtc("2030-01-12", "24:00", LA)).toEqual({ ok: false, error: "invalid" });
    expect(labLocalToUtc("12/01/2030", "08:00", LA)).toEqual({ ok: false, error: "invalid" });
    expect(labLocalToUtc("2030-01-12", "8am", LA)).toEqual({ ok: false, error: "invalid" });
  });
});

describe("labLocalToUtc in Asia/Tokyo (no daylight saving)", () => {
  it("is always nine hours ahead", () => {
    expect(labLocalToUtc("2030-01-12", "09:00", TOKYO)).toEqual({
      ok: true,
      utc: utc("2030-01-12T00:00:00Z"),
      ambiguous: false,
    });
    expect(labLocalToUtc("2030-07-12", "00:30", TOKYO)).toEqual({
      ok: true,
      utc: utc("2030-07-11T15:30:00Z"),
      ambiguous: false,
    });
  });

  it("keeps the same offset on Los Angeles change days", () => {
    expect(offsetMinutes(utc("2030-03-10T10:00:00Z"), TOKYO)).toBe(540);
    expect(offsetMinutes(utc("2030-11-03T09:30:00Z"), TOKYO)).toBe(540);
  });
});

describe("display helpers", () => {
  it("formats an instant in the lab zone with its abbreviation", () => {
    expect(formatInLab("2030-01-12T16:00:00Z", LA)).toBe("Sat, Jan 12, 08:00 PST");
    expect(formatInLab("2030-07-12T15:00:00Z", LA)).toBe("Fri, Jul 12, 08:00 PDT");
  });

  it("gives the local calendar date", () => {
    // 07:30 UTC on the 14th is still the evening of the 13th in Los Angeles.
    expect(labDateKey(utc("2030-01-14T07:30:00Z"), LA)).toBe("2030-01-13");
    expect(labDateKey(utc("2030-01-14T07:30:00Z"), TOKYO)).toBe("2030-01-14");
  });
});

describe("hasHalfHourOffsets", () => {
  const now = utc("2030-01-01T00:00:00Z");

  it.each([LA, TOKYO, "Asia/Kolkata", "Australia/Adelaide", "Australia/Lord_Howe", "UTC"])(
    "accepts %s",
    (zone) => {
      expect(hasHalfHourOffsets(zone, now)).toBe(true);
    },
  );

  it.each(["Asia/Kathmandu", "Australia/Eucla", "Pacific/Chatham", "Not/AZone"])(
    "rejects %s",
    (zone) => {
      expect(hasHalfHourOffsets(zone, now)).toBe(false);
    },
  );
});
