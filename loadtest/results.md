# Load-test results: availability cache

Two runs, k6 v2.3.0, three repeats of every configuration:
- `20261111T012740Z` at commit `edfc4d0`: capacity, fixed-rate availability and browse servers, booking races, and hit rate.
- `20261111T041745Z` at commit `76f7f42`: fixed-rate slot search only, rerun at a rate the uncached stack sustains (see [below](#why-the-slot-search-run-was-replaced)). The application code is the same as in the first run; only the load-test tooling changed.

Tables show the median of the three repeats, plus the range where it matters. Method: [docs/decisions.md, "Load-test method"](../docs/decisions.md). Commands: `python3 loadtest/run.py`, then `python3 loadtest/report.py loadtest/out/<run>`.

## Summary

- **Availability:** at a fixed 161 req/s, the cache cut Postgres CPU from 29% to 8% of a core (−73%). p50 went from 4.5 to 3.8 ms and p95 from 210 to 160 ms. The highest rate held with p95 ≤ 500 ms rose from 180 to 225 req/s.
- **Slot search:** at a fixed 5.9 req/s, which the uncached stack sustains, Postgres CPU fell from 6% to 3% (−39%), and latency was the same (p50 about 39 ms, p95 about 120 ms). The gain shows under load: the highest rate held with p95 ≤ 500 ms rose from 14 to 27 req/s.
- **Browse servers** doesn't read availability and serves as the control. Its numbers moved by no more than the spread between repeats.
- **The API process is the bottleneck in every configuration**, not Postgres. With the cache on, Postgres has plenty of headroom, and the next gain is in the API's own CPU time.
- **Booking race:** 6 of 6 races ended with exactly 4 × 201 and 96 × 409, and the database check passed every time. That only holds since the deadlock fix described [below](#finding-booking-races-deadlocked-on-the-exclusion-constraint).
- **Hit rate:** 100.0% once warm (9 misses in about 99,000 day lookups), and 98.8–99.6% while warming up.
- **401 and 429 responses:** 0 across all runs. Either one aborts the run.

## Setup

| | |
|---|---|
| Host | Apple M1 (8 cores, 8 GiB), macOS, OrbStack with 8 CPUs and 4 GiB for Docker |
| Stack | `docker-compose.loadtest.yml`: postgres 1.0 CPU / 768 MiB, API 0.75 CPU / 512 MiB (one uvicorn process), redis 0.25 CPU / 128 MiB; no worker |
| Data | Deterministic seed, reset before every run: 3 labs, 31 servers, 351 users, 11,148 reservations, 27,991 allocations (5,413 active), over 60 days of history and 14 days ahead |
| Load | Open model. A `ramping-arrival-rate` capacity phase per configuration runs 10 steps of 12 s each. Then a `constant-arrival-rate` latency phase runs at the same rate with the cache off and on: 30 s warm-up, reported separately, then 90 s measured. k6's random choices come from a fixed seed per VU. The API is restarted before every run. |

**About the limits:** they add up to 2 vCPUs and 2 GiB, the size of a t4g.small. A t4g.small only runs at 2 full vCPUs while it has burst credits. Its sustained baseline is 20% of each vCPU, so these numbers reflect burst capacity, not what the instance holds all day. The rates that matter for the deployment are the relative ones: cache off against cache on.

**Caveats:**
- k6 runs on the same host as the containers, outside their limits. The host has spare cores, but some noise from that sharing is expected. The browse-servers control shows how large it is: its p95 varied from 70 to 344 ms across repeats.
- CPU is the mean `docker stats` percentage during the measured phase, where 100% means one core. The API's limit is 75%.
- "Busy conns" is the count of non-idle connections in `pg_stat_activity`, sampled 4 times a second. The pool holds 10.

## Fixed rate, cache off vs on

Each rate is the same with the cache off and on. Availability and browse servers ran at 65% of their uncached peak in the capacity phase; both sustain that rate without failures. Slot search ran at 65% of its median uncached rate sustained with no failed request (see [below](#why-the-slot-search-run-was-replaced)).

| Script | Rate (req/s) | Cache | Postgres CPU | API CPU | p50 ms | p95 ms | p99 ms | Errors | Dropped | Busy conns (mean / max) | p95 range |
|---|---|---|---|---|---|---|---|---|---|---|---|
| availability | 161 | off | **29%** | 54% | 4.5 | 210 | 446 | 0.00% | 48 | 1.5 / 10 | 177 to 426 |
| availability | 161 | on | **8%** | 60% | 3.8 | 160 | 373 | 0.00% | 52 | 1.4 / 10 | 117 to 374 |
| | | change | **−73%** | +10% | −15% | −24% | −16% | | | | |
| slot_search | 5.9 | off | **6%** | 30% | 39.8 | 123 | 228 | 0.00% | 0 | 0.3 / 2 | 122 to 147 |
| slot_search | 5.9 | on | **3%** | 30% | 38.4 | 120 | 230 | 0.00% | 0 | 0.3 / 1 | 116 to 1,247 |
| | | change | **−39%** | 0% | −4% | −3% | +1% | | | | |
| browse_servers (control) | 209 | off | 10% | 56% | 3.0 | 89 | 169 | 0.00% | 25 | 1.1 / 10 | 71 to 344 |
| browse_servers (control) | 209 | on | 10% | 58% | 3.1 | 96 | 236 | 0.00% | 11 | 1.1 / 10 | 90 to 211 |

Rates are in requests per second. k6 counts iterations, and browse servers makes two requests per iteration (a list, then one server), so 104.5 iterations per second. "Dropped" counts iterations k6 couldn't start on time because all its VUs were busy, out of about 14,500 per availability run. In the slot-search runs, every measured response was a 200 (531 or 532 per run); status codes were recorded per phase from this run on.

**Reading it:**
- **Availability is a cheap query** even without the cache: it reads one server's allocations over a short window. The cache's main effect there is on Postgres, which spends about a quarter as much CPU. Latency improves less, because most of a request's time is spent in the API (authentication, serialization) either way. API CPU rose by 10% with the cache on, which is the cost of decoding cached entries. That is a trade worth making on this deployment, since a single instance runs both processes.
- **Slot search reads every eligible server's allocations** over the search window. At a light load, the database part of that is fast, so the cache halves Postgres CPU but doesn't change latency. The time goes to the API's own work, which is the same either way. The benefit appears when Postgres becomes the constraint: in the capacity phase, the sustained rate nearly doubles (14 to 27 req/s), and in the replaced overloaded run below, latency fell by 69–83%.
- **One cache-on slot-search run had a p95 of 1,247 ms**, against 116 and 120 ms for the other two. Its p50 was normal (39 ms), and nothing failed. A tail like this from a single run doesn't recur and isn't explained by these measurements. The median is unaffected.

### Why the slot-search run was replaced

The first run set slot search's fixed rate to 65% of its uncached peak: 28.3 req/s. That peak counted every completed request, including fast failures. The uncached stack was already shedding load at that rate, so the run measured an overloaded system:

| Script | Rate (req/s) | Cache | Postgres CPU | API CPU | p50 ms | p95 ms | p99 ms | Errors | Busy conns (mean / max) | p95 range |
|---|---|---|---|---|---|---|---|---|---|---|
| slot_search | 28.3 | off | 9% | 77% | 632 | 1,769 | 2,577 | 0.24% | 6.8 / 10 | 845 to 3,688 |
| slot_search | 28.3 | on | 5% | 70% | 109 | 554 | 750 | 0.00% | 3.3 / 10 | 373 to 1,322 |

The comparison was fair in the sense that both configurations faced the same offered load. But it measured queueing rather than service time, which is exactly what the fixed-rate comparison is meant to avoid. The failures' status codes weren't recorded then. Their latency levels off near the pool's 3 s checkout timeout, which points to 503 `database-busy`.

The rerun takes the rate from the same capacity results with a stricter rule: the highest step with p95 ≤ 500 ms, at least 95% of requests completed, and no failed request at all. The three uncached runs sustained 4.5, 9.0 and 22.5 req/s by that rule; the median is 9.0, and 65% of it is 5.9 req/s. In one run the rule cut the rate from 13.5 to 9.0 req/s because of a single connection closed without a response right after the API restart. The rule is applied as defined, even so. The runner now derives the rate this way (`--capacity-from` reuses an earlier run's capacity results), and every script records responses by status code per step and phase.

### Warm-up at the fixed rate

The first 30 s after an API restart, with a cache that's empty at the start. This phase is excluded from the tables above.

| Script | Cache | p50 ms | p95 ms |
|---|---|---|---|
| availability | off | 4.5 | 218 |
| availability | on | 3.9 | 251 |
| slot_search | off | 40.0 | 165 |
| slot_search | on | 37.6 | 111 |
| browse_servers | off | 3.0 | 97 |
| browse_servers | on | 3.0 | 81 |

The cold cache costs availability a little at p95, since the first request for each server and day goes to Postgres and then writes Redis. Slot search fills its entries within a few requests, because one search covers many servers.

## Capacity

Ramping arrival rate, from 10% to 100% of a ceiling set at about twice the throughput seen in trial runs. A step counts as sustained if p95 ≤ 500 ms, at least 95% of the offered requests were completed, and at most 1% failed. The sustained rate is the highest step before the first one that misses.

| Script | Cache | Peak req/s | Peak range | Sustained req/s | Sustained range |
|---|---|---|---|---|---|
| availability | off | 248 | 229 to 258 | 180 | 135 to 225 |
| availability | on | 277 | 263 to 302 | **225** | 225 to 270 |
| slot_search | off | 44 | 43 to 44 | 14 | 4 to 22 |
| slot_search | on | 43 | 41 to 43 | **27** | 22 to 27 |
| browse_servers | off | 322 | 297 to 342 | 300 | 250 to 350 |
| browse_servers | on | 348 | 330 to 367 | 300 | 300 to 350 |

All 18 capacity runs saturated before their last step. Peak counts every completed request, including failures. For slot search, the peak is the rate at which the stack answers, partly with fast failures, not the rate it serves well. That is why the peaks are equal while the sustained rates differ by almost 2×. Per step, medians:

`availability`

| Offered req/s | Achieved off | Achieved on | p95 off ms | p95 on ms | Errors off | Errors on |
|---|---|---|---|---|---|---|
| 45 | 45 | 45 | 17 | 13 | 0.0% | 0.0% |
| 90 | 90 | 90 | 169 | 17 | 0.0% | 0.0% |
| 135 | 135 | 135 | 150 | 94 | 0.0% | 0.0% |
| 180 | 179 | 179 | 270 | 274 | 0.0% | 0.0% |
| 225 | 222 | 225 | 511 | 235 | 0.0% | 0.0% |
| 270 | 246 | 266 | 2,454 | 500 | 2.2% | 0.0% |
| 315 | 233 | 277 | 2,825 | 1,674 | 3.8% | 0.3% |
| 360 | 229 | 260 | 2,894 | 2,206 | 4.3% | 0.6% |
| 405 | 216 | 249 | 2,937 | 2,389 | 4.5% | 0.6% |
| 450 | 242 | 266 | 2,747 | 2,185 | 2.6% | 0.5% |

`slot_search`

| Offered req/s | Achieved off | Achieved on | p95 off ms | p95 on ms | Errors off | Errors on |
|---|---|---|---|---|---|---|
| 4 | 4 | 4 | 108 | 106 | 0.0% | 0.0% |
| 9 | 9 | 9 | 146 | 178 | 0.0% | 0.0% |
| 14 | 14 | 14 | 118 | 101 | 0.0% | 0.0% |
| 18 | 18 | 18 | 347 | 167 | 0.0% | 0.0% |
| 22 | 22 | 22 | 386 | 165 | 0.0% | 0.0% |
| 27 | 27 | 27 | 604 | 373 | 0.0% | 0.0% |
| 32 | 31 | 32 | 3,271 | 731 | 5.1% | 0.0% |
| 36 | 34 | 34 | 3,454 | 3,089 | 16.5% | 5.4% |
| 40 | 39 | 38 | 3,472 | 3,114 | 32.9% | 12.9% |
| 45 | 44 | 43 | 3,548 | 3,439 | 26.6% | 19.1% |

`browse_servers`

| Offered req/s | Achieved off | Achieved on | p95 off ms | p95 on ms | Errors off | Errors on |
|---|---|---|---|---|---|---|
| 50 | 50 | 50 | 13 | 12 | 0.0% | 0.0% |
| 100 | 100 | 100 | 7 | 7 | 0.0% | 0.0% |
| 150 | 150 | 150 | 43 | 21 | 0.0% | 0.0% |
| 200 | 200 | 200 | 90 | 44 | 0.0% | 0.0% |
| 250 | 248 | 248 | 148 | 170 | 0.0% | 0.0% |
| 300 | 297 | 295 | 327 | 296 | 0.0% | 0.0% |
| 350 | 294 | 330 | 1,246 | 693 | 0.1% | 0.0% |
| 400 | 294 | 344 | 1,619 | 1,525 | 0.1% | 0.1% |
| 450 | 293 | 345 | 1,743 | 1,506 | 0.1% | 0.0% |
| 500 | 320 | 328 | 1,652 | 1,527 | 0.0% | 0.0% |

Past saturation, p95 levels off at about 3 s with the cache off or on. That matches the pool's checkout timeout, after which a request that can't get a connection gets 503 `database-busy` with `Retry-After` instead of queueing. These capacity runs predate status-code counting, so the 503s are inferred from latency, not counted. One slot-search capacity run (cache off, repeat 2) also logged a single connection closed without a response (`EOF`) on its first request, right after the API restart.

## Booking race

100 students book the same 2-GPU slot on one 8-GPU server at once, so exactly 4 can win. After each race, `python -m app.loadtest verify` checks the database.

| Cache | Run | 201 | 409 | Other errors | p50 ms | p95 ms | Winners (expected) | Overlapping active allocations | Bookings per GPU | Check |
|---|---|---|---|---|---|---|---|---|---|---|
| off | 1 | 4 | 96 | 0 | 501 | 671 | 4 (4) | 0 | 1 on all 8 | pass |
| off | 2 | 4 | 96 | 0 | 453 | 1,127 | 4 (4) | 0 | 1 on all 8 | pass |
| off | 3 | 4 | 96 | 0 | 485 | 654 | 4 (4) | 0 | 1 on all 8 | pass |
| on | 1 | 4 | 96 | 0 | 463 | 605 | 4 (4) | 0 | 1 on all 8 | pass |
| on | 2 | 4 | 96 | 0 | 425 | 567 | 4 (4) | 0 | 1 on all 8 | pass |
| on | 3 | 4 | 96 | 0 | 518 | 681 | 4 (4) | 0 | 1 on all 8 | pass |

Latency here is the serialization at work: bookings on one server take turns, so the 100th request waits for the 99 before it. That cost is acceptable, because real traffic rarely puts more than a few bookings on one server at once.

## Finding: booking races deadlocked on the exclusion constraint

The first trial races exposed a correctness and availability problem that predates the cache. The race above only passes because of the fix.

- **What happened:** in 2 of 5 trial races, 82–88% of requests failed with 500 after about 30 s, and only 2 of the 4 possible bookings were made. Nothing was double-booked: the exclusion constraint held. With `log_lock_waits` on, Postgres logged repeated deadlocks between two allocation inserts, `while checking exclusion constraint`.
- **Why:** an exclusion constraint is checked after the new row is already in the table and its index. Two transactions inserting the same GPU at the same moment can each find the other's uncommitted row and wait for it. Postgres breaks the cycle only after `deadlock_timeout` (1 s). The loser retries and collides again. Every booking held its pool connection the whole time, so the other requests waited SQLAlchemy's default 30 s for a connection and failed with an unhandled error. Inserting GPUs in ascending order, which the design relied on to prevent deadlocks, decides which GPU the two inserts collide on, not whether they do.
- **Fix:**
  - A booking now locks its server row `FOR NO KEY UPDATE` after the user's advisory lock, so bookings on one server run one at a time. Each one sees the previous winner's commit, and losers get 409 `gpu-unavailable` on their first attempt.
  - The exclusion constraint and the deadlock retry stay as backstops.
  - An audit of every path that takes the user lock or the server row (create, approve, cancel, reject, deactivation, both sweep jobs, server changes) found no other order, so the deadlock didn't just move somewhere else.
  - Separately, the pool checkout timeout is now 3 s, and a timeout returns 503 `database-busy` with `Retry-After: 2` instead of a 500 after 30 s.
- **Evidence:**
  - A new integration test runs 10 rounds of 12 concurrent 2-GPU bookings on an 8-GPU server. It requires 4 winners per round, every loser refused as unavailable, and no deadlocks in `pg_stat_database`. On the old code it failed in every round: one run counted 9 deadlocks in 10 rounds, and losers ran out of retries in 9.
  - Separate tests show that the constraint alone and the server lock alone each still prevent double booking, and that with both removed, bookings double-book.
  - After the fix, 20 consecutive smoke races ended with 4 × 201 and 96 × 409, with no deadlock and no lock wait over 1 s in Postgres's log. So did the 6 timed races above.
- The full reasoning and the lock log are in [docs/decisions.md, "Revised in Phase 9"](../docs/decisions.md).

## Cache hit rate

Measured in separate untimed runs with `AVAILABILITY_CACHE_STATS` on. Counting adds a Redis round trip per request, so it is off during timed runs. Counted per server and day entry.

| Script | Warm-up hits / misses | Warm-up rate | Measured hits / misses | Measured rate |
|---|---|---|---|---|
| availability | 35,495 / 449 | 98.8% | 99,247 / 9 | 100.0% |
| slot_search | 84,989 / 380 | 99.6% | 248,736 / 7 | 100.0% |

The read-only scripts never write, so the generations never change, and each run (2 minutes) is shorter than the 300 s TTL. The few misses in the measured phase are server and day combinations that hadn't been requested yet. In production, every booking, cancellation, or server change on a server starts a new generation for it, so the real hit rate depends on the ratio of reads to writes for each server. The race above checks that a write is seen immediately.

## Follow-ups

- **Profile slot search in the API.** Its time is spent in the API process, not in Postgres: 30% of a core at 5.9 req/s with the cache off or on, and 70–77% (at the 75% limit) at 28 req/s. Decoding many day entries per server and computing free windows are the likely costs. A profile would show where the time goes.
- **Faster cache serialization.** Cached entries are JSON encoded and decoded with the standard library, and that cost shows: availability's API CPU is 10% higher with the cache on. orjson or msgpack would cut it. Either is a new dependency, so it needs its own decision, and it should come after the profile confirms that decoding is where the time goes.
- **Run on the target instance.** These numbers use burst capacity on a laptop. A run on a t4g.small, in both burst and baseline, would give absolute numbers for capacity planning.
