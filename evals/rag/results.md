# Retrieval results

## Rules for the reported number
- The **first** acceptance run, made after the parameters are chosen on the dev set, is recorded here verbatim as the reported result. Later runs are a regression gate only and never replace it.
- If the first run is below 27/30, it is recorded as is. Improvements may only come from changes validated on the dev set. Acceptance is then re-run **once**, and both numbers stay here with what changed in between.
- Any edit to `kb/` made after the first acceptance run is logged under "KB edits after the first run" (date, file, section, why).

## Question sets

### Acceptance set (`acceptance.jsonl`)
- 30 questions, labeled at the section level (`document` + full heading path), against the knowledge base outline (headings only, no body text).
- 10 questions (`author: blind`) were written from the topic list alone, before any document text existed. 20 (`author: informed`) were written from the rules the code enforces and from student situations.
- Mix: 10 direct, 10 situation, 6 colloquial, 4 that span two documents.
- **Author knowledge.** The knowledge base bodies were written by an author who had already seen the acceptance questions (all 30 were frozen before the bodies existed). Body overlap is measured and reported below so the effect can be judged; two sentences that echoed acceptance wording were reworded before any retrieval run (logged under "Question-set notes").
- Heading-overlap check (`overlap.py`) before freezing: every gold label exists; median heading share 0.00; one question flagged and rewritten (below). Body overlap is reported once the document bodies exist.

### Question-set notes
- **b09 rewritten before freezing** (heading-overlap flag: 2 of 3 content words of its gold heading "Getting hours back").
  - Original: "My job crashed 20 minutes into a 6-hour slot. If I stop the reservation now, does the unused time go back into my weekly budget?"
  - New: "My job crashed 20 minutes into a slot that was booked until 6pm. If I stop the reservation now, does the unused time return to my weekly budget?"
  - Labels and author unchanged.
- **Body overlap after the document bodies were written** (`overlap.py --body`: share of a question's content words found in its gold section). First measurement: median 0.33, max 0.83. Blind questions median 0.31, informed median 0.33.
  - Two body sentences echoed acceptance wording and were rephrased **before any retrieval run**. Questions and labels were not changed.
    - `hardware-guide › Estimating GPU memory › Full fine-tuning` repeated "updating every weight … with Adam in mixed precision" from i13. It now reads "Full fine-tuning trains all parameters. With AdamW and 16-bit mixed-precision training …". i13 went from 0.83 to 0.58.
    - `hardware-guide › Multi-GPU jobs › When NVLink matters` described tensor parallelism as "one model is split across GPUs", echoing i12. It now reads "divides each layer's weight matrices among the GPUs". i12 went from 0.71 to 0.57.
  - After the rephrasing: median 0.33, max 0.60.

### Dev set (`dev.jsonl`)
- 20 questions for tuning, written after the document bodies, labeled the same way. They never overlap the acceptance questions.
- Heading-overlap check: five questions flagged and reworded before any run (d03, d09, d15, d16, d20). d09 is still flagged because it quotes the error type `too-many-gpus`, which is what a student would paste; kept. Median heading share after rewording 0.00; median body share 0.44, higher than the acceptance set (0.33), as expected for questions written after the text.

### Negatives (`negatives.jsonl`)
- 10 questions the knowledge base does not answer, used only to measure the minimum score. Six are near the domain (another university's supercomputer, Slurm `sbatch`, a CUDA device-side assert, building opening hours, installing PyTorch with CUDA on a laptop, conference deadlines); four are off topic.
- Re-checked against the finished bodies: none is answered. Closest wording: the contact guide mentions administrators' "office hours" (n04 asks when a building opens), the FAQ says drivers are installed by administrators (n05 asks about a personal laptop), and the quota page mentions conference deadlines as a busy time (n06 asks which deadlines are coming up).

## Embeddings
- Recorded 2026-11-08 with `python -m app.rag.evaluation record`: 292 distinct texts (every chunk text for each sweep setting, plus all 60 questions), **19,225 tokens billed** as reported by the API's `usage` field, `text-embedding-3-small` at 1536 dimensions.
- Cost at the list price of $0.02 per 1M tokens: **about $0.0004**. A full ingestion of the current knowledge base embeds 113 chunks (8,135 section tokens plus title and heading prefixes).
- Stored in `embeddings.json.gz` (1.2 MB). CI replays it and never calls the API.

## Dev sweep
Run 2026-11-08 with `python -m app.rag.evaluation sweep`, dev set only.

### Section lengths
113 sections; tokens min 12, median 67, 90th percentile 108, max 230, total 8135.

| target | overlap | chunks |
|---|---|---|
| 200 | 0 | 114 |
| 200 | 50 | 114 |
| 350 | 0 | 113 |
| 350 | 50 | 113 |
| 500 | 0 | 113 |
| 500 | 50 | 113 |
| 800 | 0 | 113 |
| 800 | 50 | 113 |

### Chunking grid (dev, k = 5, no minimum score)
| target / overlap / prefix | hits | hit@1 | hit@3 | MRR | tokens per query |
|---|---|---|---|---|---|
| 200 / 0 / on | 20/20 | 12 | 20 | 0.775 | 441 |
| 200 / 0 / off | 18/20 | 14 | 18 | 0.800 | 433 |
| 200 / 50 / on | 20/20 | 12 | 20 | 0.775 | 442 |
| 200 / 50 / off | 18/20 | 14 | 18 | 0.800 | 443 |
| 350 / 0 / on | 20/20 | 12 | 20 | 0.775 | 441 |
| 350 / 0 / off | 18/20 | 14 | 18 | 0.800 | 427 |
| 350 / 50 / on | 20/20 | 12 | 20 | 0.775 | 441 |
| 350 / 50 / off | 18/20 | 14 | 18 | 0.800 | 427 |
| 500 / 0 / on | 20/20 | 12 | 20 | 0.775 | 441 |
| 500 / 0 / off | 18/20 | 14 | 18 | 0.800 | 427 |
| 500 / 50 / on | 20/20 | 12 | 20 | 0.775 | 441 |
| 500 / 50 / off | 18/20 | 14 | 18 | 0.800 | 427 |
| 800 / 0 / on | 20/20 | 12 | 20 | 0.775 | 441 |
| 800 / 0 / off | 18/20 | 14 | 18 | 0.800 | 427 |
| 800 / 50 / on | 20/20 | 12 | 20 | 0.775 | 441 |
| 800 / 50 / off | 18/20 | 14 | 18 | 0.800 | 427 |

### k (dev, 500 / 50 / on, no minimum score)
| k | hits | hit@1 | hit@3 | MRR | tokens per query |
|---|---|---|---|---|---|
| 1 | 12/20 | 12 | 12 | 0.600 | 86 |
| 2 | 17/20 | 12 | 17 | 0.725 | 173 |
| 3 | 20/20 | 12 | 20 | 0.775 | 268 |
| 4 | 20/20 | 12 | 20 | 0.775 | 344 |
| 5 | 20/20 | 12 | 20 | 0.775 | 441 |
| 6 | 20/20 | 12 | 20 | 0.775 | 524 |
| 7 | 20/20 | 12 | 20 | 0.775 | 596 |
| 8 | 20/20 | 12 | 20 | 0.775 | 671 |
| 9 | 20/20 | 12 | 20 | 0.775 | 745 |
| 10 | 20/20 | 12 | 20 | 0.775 | 818 |

### Minimum score
Top-result score per question (cosine similarity):

- dev: d01 0.587, d02 0.548, d03 0.479, d04 0.539, d05 0.589, d06 0.719, d07 0.573, d08 0.474, d09 0.525, d10 0.460, d11 0.643, d12 0.625, d13 0.658, d14 0.532, d15 0.472, d16 0.596, d17 0.615, d18 0.455, d19 0.523, d20 0.474
- gold section score of dev hits: d01 0.587, d02 0.548, d03 0.479, d04 0.516, d05 0.544, d06 0.719, d07 0.499, d08 0.474, d09 0.525, d10 0.460, d11 0.641, d12 0.603, d13 0.645, d14 0.532, d15 0.472, d16 0.596, d17 0.615, d18 0.455, d19 0.522, d20 0.464
- negatives: n01 0.489, n02 0.561, n03 0.369, n04 0.404, n05 0.349, n06 0.315, n07 0.128, n08 0.363, n09 0.138, n10 0.131

Threshold that keeps every dev hit: 0.45. Negatives at or above it: 2 of 10 (n01, n02).

### Chosen
- chunking: target 500, overlap 50, heading prefix on (defaults kept)
- k = 5 (default kept)
- RAG_MIN_SCORE = 0.45

**Chunk size is irrelevant for this knowledge base.** The longest section is 230 tokens, so every target from 350 up produces the same 113 chunks, and 200 splits a single section. All eight chunking settings with the heading prefix on score the same (20/20, MRR 0.775). The prefix off loses two dev questions, not more than the margin of 2, so the default (on) stays. k = 3 already reaches 20/20, but no k beats the default by more than 2, so k = 5 stays.

## First acceptance run (reported result)
2026-11-08, `python -m app.rag.evaluation acceptance`, parameters from the sweep above (500 / 50 / prefix on, k = 5, `RAG_MIN_SCORE = 0.45`). Verbatim:

```
k = 5 sections, minimum score 0.45

- hit rate: 22/30 = 73.3% (required 90%: FAIL)
- hit@1: 18/30, hit@3: 22/30, MRR 0.667
- questions with two gold sections where both were returned: 2/7 (reported, not gated)
- tokens returned per question: 246

| id | rank | top result |
|---|---|---|
| b01 | miss | none |
| b02 | 1 | fair-use-and-quota / When the week starts and ends › Labs in other time zones (0.536) |
| b03 | miss | none |
| b04 | miss | none |
| b05 | miss | cancellation-and-early-release / Cancellations by admins (0.488) |
| b06 | miss | none |
| b07 | 1 | hardware-guide / Estimating GPU memory › Inference (0.584) |
| b08 | 1 | booking-rules / How far ahead you can book (0.551) |
| b09 | 1 | fair-use-and-quota / Getting hours back (0.609) |
| b10 | 1 | peak-hours / Planning around peak hours (0.511) |
| i01 | 1 | booking-rules / Time grid › Start and end times (0.589) |
| i02 | 2 | approval-policy / What happens after you request › Decision deadline (0.548) |
| i03 | 1 | fair-use-and-quota / When the week starts and ends › Bookings that cross Monday (0.761) |
| i04 | 1 | approval-policy / Which servers need approval (0.509) |
| i05 | 1 | approval-policy / Writing a good purpose (0.546) |
| i06 | 1 | approval-policy / What happens after you request › Pending requests hold GPUs (0.647) |
| i07 | miss | none |
| i08 | 1 | cancellation-and-early-release / Bookings that cannot be cancelled (0.573) |
| i09 | 1 | cancellation-and-early-release / Cancellations by admins (0.518) |
| i10 | miss | none |
| i11 | 2 | reservation-lifecycle / Calendar files (0.585) |
| i12 | 1 | hardware-guide / Multi-GPU jobs › When NVLink matters (0.540) |
| i13 | 1 | hardware-guide / Estimating GPU memory › Full fine-tuning (0.601) |
| i14 | 2 | data-and-storage / Before your booking ends (0.544) |
| i15 | 1 | data-and-storage / Sensitive and regulated data (0.554) |
| i16 | 1 | security-and-accounts / Passwords › Sign-in limits (0.563) |
| i17 | miss | none |
| i18 | 1 | citing-lab-resources / Acknowledgment text (0.663) |
| i19 | 1 | contact-and-escalation / Requesting a quota change (0.480) |
| i20 | 2 | contact-and-escalation / Hardware faults during a booking (0.698) |

Misses:
- b01 "I want 3 cards from 10 to 12 on a Wednesday morning, is that going to get rejected?" wanted peak-hours / The per-booking peak limit › Examples; peak-hours / The per-booking peak limit › How GPU-hours are calculated; got nothing
- b03 "Can I grab a machine from 8pm tonight until noon tomorrow so my training run has time to finish?" wanted booking-rules / Time grid › Minimum and maximum length; got nothing
- b04 "I already have four slots lined up and one of them is going right now. Can I add another one for Friday?" wanted booking-rules / How many reservations you can hold › What counts as active; got nothing
- b05 "my H100 booking vanished from my upcoming list and the status changed, it was still waiting for an admin and i never cancelled it. what happened??" wanted approval-policy / What happens after you request › Decision deadline; reservation-lifecycle / Automatic transitions › Expiry of undecided requests; got cancellation-and-early-release / Cancellations by admins; faq / Approvals; reservation-lifecycle / Statuses › Completed; reservation-lifecycle / Statuses › Rejected, cancelled, and expired; approval-policy / What happens after you request
- b06 "got turned down for the big cards and honestly I think the reason is wrong, who do I talk to" wanted approval-policy / Rejections; contact-and-escalation / Disputes about approvals; got nothing
- i07 "My slot that was already confirmed begins tomorrow at 9 and I don't need it anymore. Can I still drop it?" wanted cancellation-and-early-release / Cancelling before the start › Approved bookings; got nothing
- i10 "do i get pinged before my slot starts or do i have to remember it myself" wanted reservation-lifecycle / Reminders; got nothing
- i17 "The A100 box I reserved for next Thursday now shows as down for servicing. Do I still have my slot?" wanted maintenance-windows / What maintenance means for bookings › Existing bookings; got nothing

(ingestion: embedded 113, unchanged 0, reindexed 0, deleted 0)
```

**22/30 (73.3%), below the 27/30 requirement.** Blind subset 5/10, informed 17/20. Seven of the eight misses returned nothing: every section scored under 0.45. b05 is the only miss that ranked the wrong sections.

## Change between the runs
- **What changed:** `RAG_MIN_SCORE` from 0.45 to 0.0 (no score cut). The sweep now applies the dev threshold only when it separates dev hits from the negatives, and reports a leave-one-out check. Nothing in `kb/`, the question sets, chunking, or k changed.
- **Dev-set evidence:** the dev threshold (0.45) does not separate in-scope from out-of-scope questions: two near-domain negatives score above it (n01 0.489, n02 0.561). A cut that cannot separate only removes in-scope results, and out-of-scope handling already sits with the assistant's prompt. At 0.0 the dev set keeps 20/20 with the same hit@1 and MRR.
- **What the dev set did not show:** Leave-one-out: 20 of 20 dev hits survive a threshold chosen without them. The dev questions were written after the bodies and sit closer to the text (median body overlap 0.44 against 0.33 for acceptance), so their scores run higher. That shift is what made the misapplied threshold (below) cost acceptance hits.
- The motive for looking at the threshold came from the first run's failure mode; the value was chosen from the dev set and negatives only, without scoring acceptance at other thresholds.

## Second acceptance run (regression gate, not the reported result)
2026-11-08, same command, `RAG_MIN_SCORE = 0.0`. Verbatim:

```
k = 5 sections, minimum score 0.00

- hit rate: 27/30 = 90.0% (required 90%: pass)
- hit@1: 20/30, hit@3: 27/30, MRR 0.778
- questions with two gold sections where both were returned: 4/7 (reported, not gated)
- tokens returned per question: 417

| id | rank | top result |
|---|---|---|
| b01 | 3 | approval-policy / Rejections (0.423) |
| b02 | 1 | fair-use-and-quota / When the week starts and ends › Labs in other time zones (0.536) |
| b03 | miss | peak-hours / Planning around peak hours (0.437) |
| b04 | miss | booking-rules / How many reservations you can hold (0.387) |
| b05 | miss | cancellation-and-early-release / Cancellations by admins (0.488) |
| b06 | 1 | contact-and-escalation / Disputes about approvals (0.377) |
| b07 | 1 | hardware-guide / Estimating GPU memory › Inference (0.584) |
| b08 | 1 | booking-rules / How far ahead you can book (0.551) |
| b09 | 1 | fair-use-and-quota / Getting hours back (0.609) |
| b10 | 1 | peak-hours / Planning around peak hours (0.511) |
| i01 | 1 | booking-rules / Time grid › Start and end times (0.589) |
| i02 | 2 | approval-policy / What happens after you request › Decision deadline (0.548) |
| i03 | 1 | fair-use-and-quota / When the week starts and ends › Bookings that cross Monday (0.761) |
| i04 | 1 | approval-policy / Which servers need approval (0.509) |
| i05 | 1 | approval-policy / Writing a good purpose (0.546) |
| i06 | 1 | approval-policy / What happens after you request › Pending requests hold GPUs (0.647) |
| i07 | 2 | cancellation-and-early-release / Cancelling before the start (0.422) |
| i08 | 1 | cancellation-and-early-release / Bookings that cannot be cancelled (0.573) |
| i09 | 1 | cancellation-and-early-release / Cancellations by admins (0.518) |
| i10 | 1 | reservation-lifecycle / Reminders (0.409) |
| i11 | 2 | reservation-lifecycle / Calendar files (0.585) |
| i12 | 1 | hardware-guide / Multi-GPU jobs › When NVLink matters (0.540) |
| i13 | 1 | hardware-guide / Estimating GPU memory › Full fine-tuning (0.601) |
| i14 | 2 | data-and-storage / Before your booking ends (0.544) |
| i15 | 1 | data-and-storage / Sensitive and regulated data (0.554) |
| i16 | 1 | security-and-accounts / Passwords › Sign-in limits (0.563) |
| i17 | 2 | contact-and-escalation / Hardware faults during a booking › Urgent problems (0.424) |
| i18 | 1 | citing-lab-resources / Acknowledgment text (0.663) |
| i19 | 1 | contact-and-escalation / Requesting a quota change (0.480) |
| i20 | 2 | contact-and-escalation / Hardware faults during a booking (0.698) |

Misses:
- b03 "Can I grab a machine from 8pm tonight until noon tomorrow so my training run has time to finish?" wanted booking-rules / Time grid › Minimum and maximum length; got peak-hours / Planning around peak hours; approval-policy / What happens after you request › Decision deadline; fair-use-and-quota / When the week starts and ends › Bookings that cross Monday; data-and-storage / Before your booking ends; cancellation-and-early-release / Ending a running booking early › Effect on your quota
- b04 "I already have four slots lined up and one of them is going right now. Can I add another one for Friday?" wanted booking-rules / How many reservations you can hold › What counts as active; got booking-rules / How many reservations you can hold; fair-use-and-quota / When the week starts and ends › Bookings that cross Monday; onboarding / Finding hardware › Finding a slot; peak-hours / The per-booking peak limit › Examples; faq / Booking
- b05 "my H100 booking vanished from my upcoming list and the status changed, it was still waiting for an admin and i never cancelled it. what happened??" wanted approval-policy / What happens after you request › Decision deadline; reservation-lifecycle / Automatic transitions › Expiry of undecided requests; got cancellation-and-early-release / Cancellations by admins; faq / Approvals; reservation-lifecycle / Statuses › Completed; reservation-lifecycle / Statuses › Rejected, cancelled, and expired; approval-policy / What happens after you request

(ingestion: embedded 0, unchanged 113, reindexed 0, deleted 0)
```

**27/30 (90.0%), exactly at the requirement.** Blind subset 7/10 (hit@1 6), informed 20/20 (hit@1 14).
- b03 asks for an overnight booking; the length limit section loses to peak-hours and quota sections.
- b04 retrieves the parent section "How many reservations you can hold" at rank 1 but not its H3 "What counts as active", which is the gold label.
- b05 ranks five lifecycle and cancellation sections ahead of the expiry sections.
- With no cut, every negative returns five sections; the assistant has to decline them.

| run | `RAG_MIN_SCORE` | hits | blind | hit@1 | MRR | tokens per question |
|---|---|---|---|---|---|---|
| first (reported) | 0.45 | 22/30 | 5/10 | 18 | 0.667 | 246 |
| second | 0.00 | 27/30 | 7/10 | 20 | 0.778 | 417 |

### How to read the re-run
- **It is not a held-out result.** The threshold change was prompted by the acceptance failure. The dev set was 20/20 at 0.45 and did not reveal the problem, so the second run measures a setting chosen after seeing the acceptance set fail. The first run (22/30) remains the reported number.
- **0.45 misapplied the rule agreed before the sweep.** That rule said that if the dev and negative distributions overlap, keeping every dev hit wins, and the threshold cannot separate them. Near-domain negatives reach 0.561 (n02), above the dev minimum of 0.455 (d18), so the distributions overlap and no threshold separates them. Setting the cut at the dev minimum anyway kept every dev hit but filtered nothing reliably. 0.0 is what the rule should have produced in the first place; the sweep now encodes it.

## KB edits after the first run
None yet.

## Fresh blind set and query rewriting (Phase 7)

`blind.jsonl` holds 20 new questions, written as students would ask them and committed before they were labeled. They were labeled afterwards against the document headings only. The acceptance set was not touched.

### How blind it is
- **Blind to the wording, not to the rules.** No document body was read before the questions were written and labeled. But the author had read the design notes and `kb/README.md`, which state the rules the documents describe (quota size, the time grid, expiry of undecided requests, early release). So these questions are like the earlier "informed" ones in knowing what the rules are, and like the "blind" ones in not knowing how the documents phrase them.
- **Labels came from headings.** Four questions share half or more of their content words with a gold heading (n08, n10, n15, n17), mostly because the natural word is also the heading's word ("password", "maintenance", "acknowledge"). They were not rewritten, since the set was frozen first.
- **n18** (running a crypto miner) has no section that clearly answers it, so it has no label and is left out of the hit rate.
- 19 labeled questions is a small sample: one question is about five points.

### What was compared
The assistant does not embed the user's message. It calls `search_policies` with a query it writes itself, so it already "rewrites" every question. Two conditions, both through the production retrieval (pgvector, k = 5, no minimum score) on the development database with `text-embedding-3-small`:
- **raw**: the question as written.
- **rewritten**: the `query` argument the assistant model (`gpt-5.4-mini-2026-03-17`, the production prompt and tools) produced when made to call `search_policies` for that question.

| condition | hit@5 | hit@1 | MRR |
|---|---|---|---|
| raw | 17/19 | 8/19 | 0.585 |
| rewritten | 17/19 | 9/19 | 0.645 |

- **The same hit rate, with different misses.** Raw misses n01 (a three-day whole-node booking) and n11 (does a cancelled running booking count); the rewritten queries find both at ranks 1 and 2. Rewritten misses n08 (the query drifted to "who to contact") and n12 (it asked about quota rather than where the booking ends); raw finds both.
- **Ranks improve a little** (hit@1 +1, MRR +0.06), within the two-question margin used for every other retrieval decision.
- **Decision:** no separate rewriting step before retrieval. Both conditions find a gold section for 17/19 questions; at rank 1 the assistant's query does slightly better (hit@1 9/19 against 8/19 for the raw question), which is within the margin. The assistant already writes its own query, and an extra model call per search would add latency and cost for no measurable gain on this set. The useful lesson is that the model's query sometimes adds context the user did not ask about (n08, n12); the prompt tells it to use a self-contained question in the policies' terms.
- **Higher than the Phase 6 blind subset (7/10)**, likely because the author knew the rules, as above. The roughly 70% estimate for questions from people who have never read anything about the rules stands.
- **Cost:** the 19 model calls used 27,429 input and 725 output tokens (reasoning included), at most $0.024 at list prices; the embeddings are negligible. The run was done by hand against the live API; replaying it from recorded embeddings belongs with the evaluation runner.
