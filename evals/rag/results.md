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

## KB edits after the first run
None yet.
