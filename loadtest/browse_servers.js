// Browsing servers: the list with a random filter, then one server's detail. Neither
// endpoint uses the availability cache; this is the control.
import { check } from "k6";
import { API, record, headers, http, random, stepTags, labs, pick, servers, studentToken, loadOptions, writeSummary } from "./lib/common.js";

export const options = loadOptions();

const MODELS = ["RTX_4090", "RTX_6000_ADA", "L40S", "A100_40GB", "A100_80GB", "H100_80GB"];

function listQuery() {
  switch (Math.floor(random() * 4)) {
    case 0:
      return "";
    case 1:
      return `&gpu_model=${pick(MODELS)}`;
    case 2:
      return `&min_gpus=${pick([2, 4, 8])}`;
    default:
      return `&lab_id=${pick(labs)}`;
  }
}

export default function () {
  const auth = { headers: headers(studentToken()) };
  const listTags = stepTags("GET /servers");
  const list = record(http.get(`${API}/servers?page_size=20${listQuery()}`, { ...auth, tags: listTags }), listTags);
  check(list, { "list 200": (r) => r.status === 200 });
  const oneTags = stepTags("GET /servers/{id}");
  const one = record(http.get(`${API}/servers/${pick(servers).id}`, { ...auth, tags: oneTags }), oneTags);
  check(one, { "detail 200": (r) => r.status === 200 });
}

export const handleSummary = writeSummary;
