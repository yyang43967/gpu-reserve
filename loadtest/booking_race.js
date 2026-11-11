// A booking race: every racer asks for the same two GPUs-worth of the same hour on
// one 8-GPU server, all at once. Exactly four can win. 201 and 409 are both expected
// answers; anything else is an error. Correctness is checked afterwards in the
// database by `python -m app.loadtest verify` (the runner does this).
import { check } from "k6";
import { Counter } from "k6/metrics";
import { API, headers, http, race, racers, record, reported, writeSummary } from "./lib/common.js";

const won = new Counter("race_won");
const lost = new Counter("race_lost");

export const options = {
  scenarios: {
    race: {
      executor: "per-vu-iterations",
      vus: racers.length,
      iterations: 1,
      maxDuration: "60s",
      tags: { phase: "measure" },
    },
  },
  thresholds: {
    unauthorized: [{ threshold: "count==0", abortOnFail: true }],
    rate_limited: [{ threshold: "count==0", abortOnFail: true }],
    ...reported("phase:measure"),
    race_won: ["count>=0"],
    race_lost: ["count>=0"],
  },
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
};

http.setResponseCallback(http.expectedStatuses(201, 409));

export default function () {
  const response = record(
    http.post(
      `${API}/reservations`,
      JSON.stringify({
        server_id: race.server_id,
        gpu_count: race.gpu_count,
        starts_at: race.starts_at,
        ends_at: race.ends_at,
        purpose: "Booking race",
      }),
      { headers: headers(racers[__VU - 1]), tags: { name: "POST /reservations" } }
    )
  );
  if (response.status === 201) won.add(1);
  if (response.status === 409) lost.add(1);
  check(response, { "201 or 409": (r) => r.status === 201 || r.status === 409 });
}

export const handleSummary = writeSummary;
