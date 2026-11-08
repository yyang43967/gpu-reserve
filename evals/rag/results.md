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

## KB edits after the first run
None yet.
