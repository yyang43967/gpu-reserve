// Shared setup for the load scripts. Environment:
//   FIXTURE      path to the JSON written by `python -m app.loadtest seed` (required)
//   BASE_URL     API origin (default http://127.0.0.1:18100)
//   VUS          concurrent virtual users (default 20)
//   WARMUP       warm-up duration, reported separately (default 30s)
//   MEASURE      measured duration (default 90s)
//   SUMMARY_OUT  where to write the end-of-test summary as JSON
import http from "k6/http";
import { SharedArray } from "k6/data";
import { Counter } from "k6/metrics";

export const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:18100";
export const API = `${BASE_URL}/api/v1`;

const fixture = JSON.parse(open(__ENV.FIXTURE));
export const servers = new SharedArray("servers", () => fixture.servers);
export const labs = new SharedArray("labs", () => fixture.labs);
export const students = new SharedArray("students", () => fixture.students);
export const racers = new SharedArray("racers", () => fixture.racers);
export const race = fixture.race;
export const generatedAt = new Date(fixture.generated_at);

// Any 401 means a token expired or the fixture is stale; any 429 means a rate limit
// is shaping the numbers. Either would make the run meaningless, so both abort it
// (see `abortOn`) instead of being counted as ordinary errors.
export const unauthorized = new Counter("unauthorized");
export const rateLimited = new Counter("rate_limited");

export function headers(token) {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
}

// Each virtual user acts as its own student, so requests are spread over accounts.
export function studentToken() {
  return students[(__VU - 1) % students.length];
}

export function check401and429(response) {
  if (response.status === 401) unauthorized.add(1);
  if (response.status === 429) rateLimited.add(1);
  return response;
}

// k6 2.x dropped the `randomSeed` option, so Math.random() cannot be seeded. This
// generator (mulberry32) gives each virtual user its own fixed sequence: the same
// requests in the same order on every run.
const SEED = 20301;
let state = null;

export function random() {
  if (state === null) state = (SEED + Math.imul(__VU, 0x9e3779b1)) >>> 0;
  state = (state + 0x6d2b79f5) >>> 0;
  let t = state;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}

export function pick(list) {
  return list[Math.floor(random() * list.length)];
}

const VUS = parseInt(__ENV.VUS || "20", 10);
const WARMUP = __ENV.WARMUP || "30s";
const MEASURE = __ENV.MEASURE || "90s";

const abortOn = [{ threshold: "count==0", abortOnFail: true }];

// A warm-up phase, then the measured phase, both at the same constant concurrency
// with no think time: the result is the throughput and latency at saturation for
// VUS concurrent clients. Every request is tagged with its phase, so the summary
// reports the two separately and the measured numbers exclude the cold start.
export function timedOptions() {
  return {
    discardResponseBodies: false,
    scenarios: {
      warmup: {
        executor: "constant-vus",
        vus: VUS,
        duration: WARMUP,
        tags: { phase: "warmup" },
      },
      measure: {
        executor: "constant-vus",
        vus: VUS,
        duration: MEASURE,
        startTime: WARMUP,
        tags: { phase: "measure" },
      },
    },
    // Thresholds on tagged submetrics make the summary include them. Only the two
    // abort conditions can fail a run.
    thresholds: {
      unauthorized: abortOn,
      rate_limited: abortOn,
      "http_req_duration{phase:warmup}": ["p(95)>=0"],
      "http_req_duration{phase:measure}": ["p(95)>=0"],
      "http_reqs{phase:warmup}": ["count>=0"],
      "http_reqs{phase:measure}": ["count>=0"],
      "http_req_failed{phase:warmup}": ["rate>=0"],
      "http_req_failed{phase:measure}": ["rate>=0"],
    },
    summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
  };
}

export function writeSummary(data) {
  const out = {};
  if (__ENV.SUMMARY_OUT) out[__ENV.SUMMARY_OUT] = JSON.stringify(data, null, 2);
  out.stdout = summaryLine(data);
  return out;
}

function summaryLine(data) {
  const lines = [];
  for (const phase of ["warmup", "measure"]) {
    const duration = data.metrics[`http_req_duration{phase:${phase}}`];
    const reqs = data.metrics[`http_reqs{phase:${phase}}`];
    const failed = data.metrics[`http_req_failed{phase:${phase}}`];
    if (!duration || !reqs) continue;
    lines.push(
      `${phase}: ${reqs.values.count} requests, ${reqs.values.rate.toFixed(1)}/s, ` +
        `p50 ${duration.values.med.toFixed(1)} ms, p95 ${duration.values["p(95)"].toFixed(1)} ms, ` +
        `failed ${(100 * (failed ? failed.values.rate : 0)).toFixed(2)}%`
    );
  }
  return lines.join("\n") + "\n";
}

export { http };
