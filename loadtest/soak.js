// Memory soak: the three read scripts at once, each at its own constant rate, for a
// long time without restarting the API. The runner samples container memory
// throughout and for a while after (run.py --soak), so growth under steady load can be
// told apart from warm-up that levels off. Environment, besides lib/common.js:
//   SOAK           duration (default 30m)
//   SOAK_RATES     iterations per second per script, e.g.
//                  browse_servers=10,availability=40,slot_search=4
// Bookings are left out: at a constant rate they would use up the seeded quota and
// free slots well before the end, and then measure rejections.
import browseServers from "./browse_servers.js";
import availability from "./availability.js";
import slotSearch from "./slot_search.js";
import { reported, writeSummary } from "./lib/common.js";

const SOAK = __ENV.SOAK || "30m";
const rates = Object.fromEntries(
  (__ENV.SOAK_RATES || "").split(",").filter(Boolean).map((item) => {
    const [name, value] = item.split("=");
    return [name, parseFloat(value)];
  })
);
const SCRIPTS = { browse_servers: "browse", availability: "lookUp", slot_search: "search" };
for (const name of Object.keys(SCRIPTS)) {
  if (!(rates[name] > 0)) throw new Error(`SOAK_RATES needs a rate for ${name}`);
}

const scenarios = {};
for (const [name, exec] of Object.entries(SCRIPTS)) {
  scenarios[name] = {
    executor: "constant-arrival-rate",
    rate: Math.round(rates[name] * 10),
    timeUnit: "10s",
    duration: SOAK,
    preAllocatedVUs: 30,
    maxVUs: 200,
    exec,
    tags: { phase: "soak", script: name },
  };
}

const abortOn = [{ threshold: "count==0", abortOnFail: true }];
export const options = {
  scenarios,
  thresholds: {
    unauthorized: abortOn,
    rate_limited: abortOn,
    dropped_iterations: ["count>=0"],
    ...reported("phase:soak"),
  },
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
};

export function browse() {
  browseServers();
}

export function lookUp() {
  availability();
}

export function search() {
  slotSearch();
}

export const handleSummary = writeSummary;
