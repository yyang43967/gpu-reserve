# Load-test results: availability cache

Three runs, k6 v2.3.0, three repeats of every configuration:
- `20261111T012740Z` at commit `edfc4d0`: capacity for all scripts, fixed-rate availability and browse servers, booking races, and hit rate.
- `20261111T041745Z` at commit `76f7f42`: fixed-rate slot search only, rerun at a rate the uncached stack sustains (see [below](#why-the-slot-search-run-was-replaced)).
- `20261111T044441Z` at commit `7f00f8f`: slot-search capacity only, rerun with a warm-up after each API restart and with status codes counted (see [Capacity](#capacity)).

The application code is the same in all three; only the load-test tooling changed. Tables show the median of the three repeats, plus the range where it matters. Method: [docs/decisions.md, "Load-test method"](../docs/decisions.md). Commands: `python3 loadtest/run.py`, then `python3 loadtest/report.py loadtest/out/<run>`.

## Summary

**The cache's value here is lower database load, not higher throughput.** Capacity is bound by the API process's CPU, and the cache doesn't change that. Slot search peaked at 41–44 req/s in all 12 capacity runs, with the cache off or on. At saturation, the API sat at its CPU limit (78% of a core against a 75% limit) while Postgres used 3–9%.

- **Postgres CPU at a fixed rate:** −73% for availability (29% → 8% of a core at 161 req/s) and −39% for slot search (6% → 3% at 5.9 req/s). In the replaced, overloaded slot-search run it was −49%.
- **Latency at sustainable rates is unchanged, within the spread between repeats.** Slot search: p50 about 39 ms and p95 about 120 ms either way. Availability: p50 4.5 → 3.8 ms and p95 210 → 160 ms, but the p95 ranges across repeats overlap (177–426 vs 117–374 ms).
- **Capacity:**
  - *Slot search:* no consistent difference. Peak 44 vs 43 req/s. The sustained rate (p95 ≤ 500 ms on the median step) was 22 → 27 req/s in one run and 22 → 18 in the other.
  - *Availability:* a small gain. Peak 248 → 277 req/s (+12%; the ranges don't overlap) and sustained 180 → 225 req/s. The sustained difference is one step, and the uncached 225 req/s step missed the target by 11 ms.
- **Why it matters anyway:** one API process can't push Postgres hard. More uvicorn workers or instances would, and that's where the cache earns its place. Uncached availability already took 36% of Postgres's core at saturation; cached, 8%. So the cache keeps Postgres from becoming the shared bottleneck when the API scales out. That is an extrapolation; these runs used one process.
- **Booking race:** the load test found that concurrent bookings on one server could deadlock on the exclusion constraint. Since the fix, 6 of 6 timed races ended with exactly 4 × 201 and 96 × 409, and the database check passed every time. See [below](#finding-booking-races-deadlocked-on-the-exclusion-constraint).
- **Browse servers** doesn't read availability and serves as the control. Its numbers moved by no more than the spread between repeats.
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
- **Slot search reads every eligible server's allocations** over the search window. At a light load, the database part of that is fast, so the cache halves Postgres CPU but doesn't change latency. The time goes to the API's own work, which is the same either way. In the replaced, overloaded run below, latency fell by 69–83% with the cache on. That shows the cache relieving a queue for database connections, not a faster service time, and the capacity runs show no consistent gain from it.
- **One cache-on slot-search run had a p95 of 1,247 ms**, against 116 and 120 ms for the other two. Its p50 was normal (39 ms), and nothing failed. A tail like this from a single run doesn't recur and isn't explained by these measurements. The median is unaffected.

### Why the slot-search run was replaced

The first run set slot search's fixed rate to 65% of its uncached peak: 28.3 req/s. That peak counted every completed request, including fast failures. The uncached stack was already shedding load at that rate, so the run measured an overloaded system:

| Script | Rate (req/s) | Cache | Postgres CPU | API CPU | p50 ms | p95 ms | p99 ms | Errors | Busy conns (mean / max) | p95 range |
|---|---|---|---|---|---|---|---|---|---|---|
| slot_search | 28.3 | off | 9% | 77% | 632 | 1,769 | 2,577 | 0.24% | 6.8 / 10 | 845 to 3,688 |
| slot_search | 28.3 | on | 5% | 70% | 109 | 554 | 750 | 0.00% | 3.3 / 10 | 373 to 1,322 |

The comparison was fair in the sense that both configurations faced the same offered load. But it measured queueing rather than service time, which is exactly what the fixed-rate comparison is meant to avoid. The failures' status codes weren't recorded then. Their latency levels off near the pool's 3 s checkout timeout, which points to 503 `database-busy`.

The rerun takes the rate from the same capacity results with a stricter rule: the highest step with p95 ≤ 500 ms, at least 95% of requests completed, and no failed request at all. The three uncached runs sustained 4.5, 9.0 and 22.5 req/s by that rule; the median is 9.0, and 65% of it is 5.9 req/s. In one run the rule cut the rate from 13.5 to 9.0 req/s because of a single connection closed without a response (`EOF`) at the 13.5 req/s step, about 30 s into the run. The rule is applied as defined, even so. The runner now derives the rate this way (`--capacity-from` reuses an earlier run's capacity results), and every script records responses by status code per step and phase.

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

Ramping arrival rate, from 10% to 100% of a ceiling set at about twice the throughput seen in trial runs, in 10 steps of 12 s.
- **Peak** is the highest achieved throughput. It is the primary figure here, because it is stable across repeats.
- **Sustained** is secondary. A step passes if p95 ≤ 500 ms, at least 95% of the offered requests were completed, and at most 1% failed. The sustained rate is the highest step that passes after taking, per step, the median of each figure across the three repeats.
  - The per-run figures (the highest step before a run's first miss) are shown for their spread. A 12 s step holds 50–300 slot searches, so one noisy p95 can end a run early: one uncached run scored 0 because its first step had a p95 of 615 ms, and its later steps were fine.
- **CPU at saturation** is the mean over the last three steps. 100% means one core, and the API's limit is 75%.

| Run | Script | Cache | Peak req/s | Peak range | Sustained req/s | Per-run sustained | API CPU at saturation | Postgres CPU at saturation |
|---|---|---|---|---|---|---|---|---|
| first | availability | off | 248 | 229 to 258 | 180 | 180, 135, 225 | 80% | 36% |
| first | availability | on | 277 | 263 to 302 | 225 | 225, 225, 270 | 83% | 8% |
| first | slot_search | off | 44 | 43 to 44 | 22 | 4, 14, 22 | 78% | 9% |
| first | slot_search | on | 43 | 41 to 43 | 27 | 22, 27, 27 | 78% | 4% |
| rerun | slot_search | off | 44 | 44 to 44 | 22 | 22, 22, 0 | 78% | 8% |
| rerun | slot_search | on | 43 | 42 to 43 | 18 | 18, 18, 9 | 78% | 6% |
| first | browse_servers | off | 322 | 297 to 342 | 300 | 350, 300, 250 | 83% | 14% |
| first | browse_servers | on | 348 | 330 to 367 | 300 | 350, 300, 300 | 82% | 14% |

**Reading it:**
- **Every configuration saturates on the API's CPU.** The API runs at its limit, or slightly above it, since `docker stats` and the CPU quota don't account in exactly the same way. Postgres has most of its core free.
- **Slot search: no consistent difference.**
  - Peak throughput is 43–44 req/s in every configuration of both runs.
  - The sustained rate moved one step in opposite directions: 22 → 27 req/s in the first run and 22 → 18 in the rerun. That is within the noise of this method.
  - The cache removes database work, which isn't the constraint. Decoding cached entries adds API work, which is.
- **Availability: a small gain.**
  - The peak rose 12%, and the ranges don't overlap.
  - The sustained difference is one step and hinges on 11 ms: the uncached 225 req/s step had a median p95 of 511 ms.
  - Without the cache, availability is the one script where Postgres does real work (36% of a core at saturation), so there is some database time to take off each request.
- **An earlier version of this file reported slot search as 14 → 27 req/s.** That used each run's own sustained rate, from the first run only. Neither the rerun nor the median-step analysis supports it.
- **Rerun method:**
  - After each API restart and seed, the runner waits for `/ready`. Then 10 concurrent clients make 3 requests each to endpoints that don't use the cache, so the pool's connections are open before k6 starts. This didn't reduce the spread.
  - The single `EOF` (status 0) came back in the rerun at the same step, about 30 s in, so it isn't a cold-start effect. See Follow-ups.

Per step, medians across repeats. First run:

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

Slot-search rerun:

| Offered req/s | Achieved off | Achieved on | p95 off ms | p95 on ms | Errors off | Errors on |
|---|---|---|---|---|---|---|
| 4 | 4 | 4 | 120 | 144 | 0.0% | 0.0% |
| 9 | 9 | 9 | 138 | 123 | 0.0% | 0.0% |
| 14 | 14 | 14 | 120 | 356 | 0.0% | 0.0% |
| 18 | 18 | 18 | 268 | 293 | 0.0% | 0.0% |
| 22 | 22 | 22 | 262 | 1,320 | 0.0% | 0.0% |
| 27 | 27 | 27 | 674 | 2,531 | 0.0% | 0.6% |
| 32 | 31 | 30 | 2,784 | 3,100 | 2.9% | 6.3% |
| 36 | 34 | 35 | 3,458 | 3,621 | 16.1% | 19.9% |
| 40 | 38 | 39 | 3,509 | 3,561 | 30.1% | 29.7% |
| 45 | 44 | 43 | 3,388 | 3,631 | 25.5% | 37.9% |

Past saturation, p95 levels off at about 3 s with the cache off or on. That is the pool's checkout timeout: a request that can't get a connection gets 503 `database-busy` with `Retry-After` instead of queueing. The rerun counted status codes, and every failure in it was a 503, except one request with no response (status 0). The first run predates the counting, but its failures level off at the same latency.

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
- **Status-0 `EOF` under load.** One slot-search request per few runs gets no response, at about the same point (the 13.5 req/s step, about 30 s in), even after a warm-up. The likely cause is a keep-alive race. uvicorn closes a connection after 5 s idle (`--timeout-keep-alive 5`). A k6 VU that waited about that long between requests can send on a connection the server is closing at that moment. In the early steps, each of the 50 pre-allocated VUs waits several seconds between requests (about 3.7 s on average at 13.5 req/s), close to that limit. To test it, raise `--timeout-keep-alive` above k6's longest idle gap (for example 75 s) in the load-test stack only, and rerun capacity. If the status-0 responses disappear, the fix belongs in the load test, or in front of the API (a proxy that keeps connections alive longer than its clients), not in the app.
- **Faster cache serialization.** Cached entries are JSON encoded and decoded with the standard library, and that cost shows: availability's API CPU is 10% higher with the cache on. orjson or msgpack would cut it. Either is a new dependency, so it needs its own decision, and it should come after the profile confirms that decoding is where the time goes.
- **Run on the target instance.** These numbers use burst capacity on a laptop. A run on a t4g.small, in both burst and baseline, would give absolute numbers for capacity planning.
