// Slot search: N GPUs (sometimes of one model) for a few hours, somewhere in a window
// of three to seven days starting up to three days ahead.
import { check } from "k6";
import { API, record, generatedAt, headers, http, random, stepTags, pick, studentToken, loadOptions, writeSummary } from "./lib/common.js";

export const options = loadOptions();

const HOUR = 3600 * 1000;
const MODELS = ["RTX_4090", "L40S", "A100_80GB", "H100_80GB"];

function body() {
  const earliest = new Date(generatedAt.getTime() + (1 + Math.floor(random() * 72)) * HOUR);
  earliest.setUTCMinutes(0, 0, 0);
  const latest = new Date(earliest.getTime() + (3 + Math.floor(random() * 5)) * 24 * HOUR);
  const request = {
    gpu_count: pick([1, 1, 2, 4]),
    duration_minutes: pick([60, 120, 240, 480]),
    earliest: earliest.toISOString(),
    latest: latest.toISOString(),
  };
  if (random() < 0.5) request.gpu_model = pick(MODELS);
  return JSON.stringify(request);
}

export default function () {
  const tags = stepTags("POST /search/slots");
  const response = record(
    http.post(`${API}/search/slots`, body(), { headers: headers(studentToken()), tags }),
    tags
  );
  check(response, { "search 200": (r) => r.status === 200 });
}

export const handleSummary = writeSummary;
