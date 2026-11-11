// Shared setup for the load scripts. Environment:
//   FIXTURE      path to the JSON written by `python -m app.loadtest seed` (required)
//   BASE_URL     API origin (default http://127.0.0.1:18100)
//   MODE         latency (default) or capacity; see loadOptions
//   RATE         latency mode: iterations per second
//   WARMUP       latency mode: warm-up duration, reported separately (default 30s)
//   MEASURE      latency mode: measured duration (default 90s)
//   RAMP_MAX     capacity mode: the last step's iterations per second
//   STEPS, HOLD  capacity mode: number of steps (10) and seconds held at each (12)
//   MAX_VUS      most concurrent iterations k6 may run (default 300)
//   SUMMARY_OUT  where to write the end-of-test summary as JSON
import http from "k6/http";
import { SharedArray } from "k6/data";
import { Counter } from "k6/metrics";
import exec from "k6/execution";

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

const MODE = __ENV.MODE || "latency";
const RATE = parseFloat(__ENV.RATE || "10");
const RAMP_MAX = parseFloat(__ENV.RAMP_MAX || "100");
export const STEPS = parseInt(__ENV.STEPS || "10", 10);
const HOLD = parseInt(__ENV.HOLD || "12", 10);
const WARMUP = __ENV.WARMUP || "30s";
const MEASURE = __ENV.MEASURE || "90s";
const PRE_ALLOCATED_VUS = 50;
const MAX_VUS = parseInt(__ENV.MAX_VUS || "300", 10);
// Each capacity step starts with this long a change of rate, which is not counted.
const STEP_RAMP_SECONDS = 1;

// k6 takes whole iterations per time unit. Counting per ten seconds keeps a tenth of
// an iteration per second of precision; k6 spaces the iterations evenly either way.
const RATE_UNIT = "10s";
const perTenSeconds = (perSecond) => Math.round(perSecond * 10);

const abortOn = [{ threshold: "count==0", abortOnFail: true }];
const reported = (tag) => ({
  [`http_req_duration{${tag}}`]: ["p(95)>=0"],
  [`http_reqs{${tag}}`]: ["count>=0"],
  [`http_req_failed{${tag}}`]: ["rate>=0"],
});

// Open-model load: k6 starts iterations at a set rate whether or not earlier ones have
// finished, so a slower server faces a growing backlog instead of slowing the clients
// down (which a fixed number of looping users would do).
//
// MODE=latency: a warm-up phase, then the measured phase, both at RATE iterations per
//   second. Cache on and off get the same RATE, below the uncached capacity, so the
//   latencies compare service time rather than queueing.
// MODE=capacity: the offered rate rises in STEPS equal steps up to RAMP_MAX, each held
//   for HOLD seconds. Requests are tagged with their step, so the summary has the
//   achieved throughput and latency at each offered rate.
//
// Thresholds on tagged submetrics are what make the summary include them. Only the two
// abort conditions can fail a run.
export function loadOptions() {
  const thresholds = { unauthorized: abortOn, rate_limited: abortOn };
  const vus = { preAllocatedVUs: PRE_ALLOCATED_VUS, maxVUs: MAX_VUS };
  let scenarios;
  if (MODE === "capacity") {
    const stages = [];
    for (let step = 1; step <= STEPS; step++) {
      const target = perTenSeconds((RAMP_MAX * step) / STEPS);
      stages.push({ target, duration: `${STEP_RAMP_SECONDS}s` });
      stages.push({ target, duration: `${HOLD}s` });
      Object.assign(thresholds, reported(`step:${step}`));
    }
    scenarios = {
      capacity: {
        executor: "ramping-arrival-rate",
        startRate: perTenSeconds(RAMP_MAX / STEPS),
        timeUnit: RATE_UNIT,
        stages,
        ...vus,
        tags: { phase: "capacity" },
      },
    };
  } else {
    const phase = (duration, startTime, tag) => ({
      executor: "constant-arrival-rate",
      rate: perTenSeconds(RATE),
      timeUnit: RATE_UNIT,
      duration,
      startTime,
      ...vus,
      tags: { phase: tag },
    });
    scenarios = { warmup: phase(WARMUP, "0s", "warmup"), measure: phase(MEASURE, WARMUP, "measure") };
    Object.assign(thresholds, reported("phase:warmup"), reported("phase:measure"));
  }
  thresholds.dropped_iterations = ["count>=0"];
  return {
    scenarios,
    thresholds,
    summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
  };
}

// Tags for a request: in capacity mode, the step whose rate it was sent at, or "ramp"
// while the rate is changing between steps.
export function stepTags(name) {
  const tags = { name };
  if (MODE !== "capacity") return tags;
  const elapsed = (Date.now() - exec.scenario.startTime) / 1000;
  const period = STEP_RAMP_SECONDS + HOLD;
  const step = Math.floor(elapsed / period) + 1;
  tags.step = elapsed % period < STEP_RAMP_SECONDS || step > STEPS ? "ramp" : String(step);
  return tags;
}

export function writeSummary(data) {
  const out = {};
  if (__ENV.SUMMARY_OUT) out[__ENV.SUMMARY_OUT] = JSON.stringify(data, null, 2);
  out.stdout = summaryLine(data);
  return out;
}

function summaryLine(data) {
  const lines = [];
  const labels = ["phase:warmup", "phase:measure"];
  for (let step = 1; step <= STEPS; step++) labels.push(`step:${step}`);
  for (const label of labels) {
    const duration = data.metrics[`http_req_duration{${label}}`];
    const reqs = data.metrics[`http_reqs{${label}}`];
    const failed = data.metrics[`http_req_failed{${label}}`];
    if (!duration || !reqs || !reqs.values.count) continue;
    lines.push(
      `${label}: ${reqs.values.count} requests, ` +
        `p50 ${duration.values.med.toFixed(1)} ms, p95 ${duration.values["p(95)"].toFixed(1)} ms, ` +
        `failed ${(100 * (failed ? failed.values.rate : 0)).toFixed(2)}%`
    );
  }
  const dropped = data.metrics.dropped_iterations;
  lines.push(`dropped iterations: ${dropped ? dropped.values.count : 0}`);
  return lines.join("\n") + "\n";
}

export { http };
