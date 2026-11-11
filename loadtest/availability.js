// Availability lookups, as the server page makes them: mostly the default next seven
// days, sometimes a window of one to seven days starting up to a week ahead.
import { check } from "k6";
import { API, record, generatedAt, headers, http, random, stepTags, pick, servers, studentToken, loadOptions, writeSummary } from "./lib/common.js";

export const options = loadOptions();

const HOUR = 3600 * 1000;

function query() {
  if (random() < 0.7) return "";
  const from = new Date(generatedAt.getTime() + Math.floor(random() * 7 * 24) * HOUR);
  from.setUTCMinutes(0, 0, 0);
  const to = new Date(from.getTime() + (1 + Math.floor(random() * 7)) * 24 * HOUR);
  return `?from=${encodeURIComponent(from.toISOString())}&to=${encodeURIComponent(to.toISOString())}`;
}

export default function () {
  const tags = stepTags("GET /servers/{id}/availability");
  const response = record(
    http.get(`${API}/servers/${pick(servers).id}/availability${query()}`, {
      headers: headers(studentToken()),
      tags,
    }),
    tags
  );
  check(response, { "availability 200": (r) => r.status === 200 });
}

export const handleSummary = writeSummary;
