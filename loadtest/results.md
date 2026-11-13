# Load-test results: availability cache

Three runs, k6 v2.3.0, three repeats of every configuration:
- `20261111T012740Z` at commit `edfc4d0`: capacity for all scripts, fixed-rate availability and browse servers, booking races, and hit rate.
- `20261111T041745Z` at commit `76f7f42`: fixed-rate slot search only, rerun at a rate the uncached stack sustains (see [below](#why-the-slot-search-run-was-replaced)).
- `20261111T044441Z` at commit `7f00f8f`: slot-search capacity only, rerun with a warm-up after each API restart and with status codes counted (see [Capacity](#capacity)).

The application code is the same in all three; only the load-test tooling changed. Tables show the median of the three repeats, plus the range where it matters. Method: [docs/decisions.md, "Load-test method"](../docs/decisions.md). Commands: `python3 loadtest/run.py`, then `python3 loadtest/report.py loadtest/out/<run>`.

These first runs used a laptop with CPU limits that add up to a t4g.small. The same scripts were later run on a twin of the production instance, on AWS hardware, through production's Caddy and Compose file: see [On a twin of the production instance](#on-a-twin-of-the-production-instance). The sections before it are the laptop runs.

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

## On a twin of the production instance

Runs from 2026-11-12 and 13 on a second t4g.small built like production, with k6 on a separate t4g.micro in the same subnet. Procedure: [loadtest/instance/README.md](instance/README.md); reasoning: [docs/decisions.md, "Load test on a twin of the instance"](../docs/decisions.md). The phases ran back to back on the driver, through [loadtest/instance/chain.sh](instance/chain.sh), stopping at the first failure.

### Instance summary

- **The twin's burst capacity is 55–65% of the laptop's,** with the same bottleneck: one uvicorn process at 100% of one of the two vCPUs, while Postgres used 6–33% of a vCPU and Caddy at most 11%. The whole instance used 118–143% of its 200% at saturation, so most of a vCPU sat idle. See [Finding: one API process uses one of two vCPUs](#finding-one-api-process-uses-one-of-two-vcpus).
- **Past saturation, successful throughput falls instead of levelling off.** Requests wait out the pool's 3 s checkout timeout and fail as 503, and the requests that succeed drop by up to 28% from the best step (availability without the cache: 112 → 81 req/s). See [Finding: throughput collapses on the pool timeout](#finding-throughput-collapses-on-the-pool-timeout).
- **The cache:** the same conclusion as on the laptop. Postgres CPU fell 75% for availability and 47% for slot search at a fixed rate. Here latency also improved, with ranges that don't overlap: availability p95 23 → 15 ms, slot search 320 → 231 ms.
- **Keep-alive:** every 502 (13 in all) came from slot search, a POST, through Caddy with production's timeouts, in an overloaded step. With Caddy's idle timeout below uvicorn's (arm B), there were none. Nobody got a response with status 0, including k6 straight to uvicorn (arm C). The evidence points to the predicted race, but the counts are small. See [Keep-alive experiment](#keep-alive-experiment).
- **Memory:** no sign of a leak in the API. Over 30 minutes and 73,795 requests, its memory stayed flat at 188–190 MiB apart from one 3 MiB step, then held level after the load stopped. See [Memory soak](#memory-soak).
- **Simulated baseline** (all containers sharing 0.4 of a vCPU, as on an instance without CPU credits): availability holds about 16 req/s at p95 ≤ 500 ms, and the best successful throughput is a quarter to a third of the burst figures.
- **Worker health-check bug:** the first baseline run never started, because the worker's health check timed out at 0.4 of a vCPU even with no load. A deploy to an instance without credits would have failed the same way. It is fixed and deployed. See [Finding: the worker's health check needed a full CPU](#finding-the-workers-health-check-needed-a-full-cpu).
- **Booking race:** 15 of 15 races ended with 4 × 201 and 96 × 409, and the database check passed every time.
- **401 and 429 responses:** 0 across all phases.

### Setup on the twin

| | |
|---|---|
| Twin | t4g.small (2 Graviton2 vCPUs, 1.93 GB visible to Docker), production's AMI and first-boot script, unlimited credit mode so no run is throttled mid-way |
| Stack | Production's `infra/deploy/compose.yml` and Caddyfile, release image `api:6481365` (what production ran then), plus `loadtest/compose.instance.yml`: `ENVIRONMENT=test`, the runner's cache settings, mail to the seeded domain suppressed, the API's port published to the driver. No per-container CPU limits (as in production); production's memory limits (API 448 MiB, worker 256 MiB, Caddy 96 MiB). The worker runs. |
| Driver | t4g.micro in the same subnet, k6 v2.3.0. Requests go to Caddy on port 80, plain HTTP on the private network, except in keep-alive arm C. |
| Source | The runner from `f9a6d52` (merged as `d249451`) for every phase up to the first baseline; `bf7f03d` (the same plus the worker health-check fix, merged as `3aab152`) for the second baseline. The application image is the same in every phase. |
| Data | The same deterministic seed as the laptop runs, reset before every run |

**Caveats:**
- **The worker's cron jobs run** during every phase, as in production: expiring pending requests, completing finished bookings, and reminders. They may change seeded data mid-run. The database is reseeded before every run, and the race's correctness check covers the invariants either way.
- **CPU** is the mean `docker stats` percentage, where 100% means one vCPU. No container has a CPU limit, so the API can reach about 100% (one process) and the instance 200%.
- **Unlimited credits** keep the burst phases at full speed. A production instance in standard mode gets these numbers only while it has credits; the simulated baseline covers the rest.

### Phases

| Phase | Results under `loadtest/out/` | What | Outcome |
|---|---|---|---|
| calibrate | `20261112T203302Z-calibrate` | Capacity, cache off, the laptop's ramp ceilings, 1 repeat | Set the twin's ceilings |
| smoke-3 | `20261112T204340Z-smoke-3` | Every kind of run, short | Passed |
| burst | `20261112T210158Z-burst` | The laptop's full matrix: capacity, fixed rate, race, hit rate; cache off and on; 3 repeats | Passed; race 6 of 6 |
| keepalive-a | `20261112T224404Z-keepalive-a` | Slot-search capacity and fixed rate, race; cache on; production's keep-alive settings | Passed; race 3 of 3 |
| keepalive-b | `20261112T230026Z-keepalive-b` | The same, Caddy 60 s and uvicorn 75 s | Passed; race 3 of 3 |
| keepalive-c | `20261112T231647Z-keepalive-c` | The same, k6 straight to uvicorn on port 8000 | Passed; race 3 of 3 |
| soak | `20261112T233309Z-soak` | Three read scripts at fixed rates for 30 min, then 10 min with no load | Passed |
| baseline | `20261113T001352Z-baseline` | Capacity at 0.4 vCPU (simulated), cache on, 1 repeat | **Failed before any load**: the worker's health check. Kept as the record of the bug. |
| baseline-2 | `20261113T004137Z-baseline-2` | The same, with the health-check fix | Passed |

The results directories are not in Git (`loadtest/out/` is ignored); the tables here come from `python3 loadtest/report.py loadtest/out/<dir>` and from the samples for the soak and the per-step status counts.

### Calibration and ramp ceilings

The laptop's ceilings (450, 250, and 45 iterations/s for availability, browse servers, and slot search) would have put most steps of every ramp past the twin's saturation, leaving one or two steps to locate the knee. So one capacity run without the cache used those ceilings first. Its best successful throughput, counting only 200 responses per step, was 99 req/s for availability, 152 req/s (76 iterations/s) for browse servers, and 15 req/s for slot search. The twin's ceilings are twice that: **200, 150, and 30 iterations/s**. Every burst and keep-alive ramp then saturated (0 of 3 runs unsaturated, for every script and configuration), with four or five steps before the knee.

The calibration's own peaks (158, 181, and 43 req/s) count fast 503s, which is why the ceilings come from successful throughput instead.

### Capacity: twin against laptop

Burst phase, ramps of 10 steps of 12 s, medians of three repeats. "Peak" counts every completed request, as in the laptop tables. "Best successful" counts 200 responses only: the best step, taking the median across repeats per step.

| Script | Cache | Twin peak req/s | Twin peak range | Laptop peak | Twin best successful | Twin sustained (per run) | Laptop sustained | Twin API CPU at saturation | Twin Postgres CPU at saturation |
|---|---|---|---|---|---|---|---|---|---|
| availability | off | 152 | 150 to 154 | 248 | 112 | 80 (80, 80, 80) | 180 | 92% | 33% |
| availability | on | 148 | 145 to 153 | 277 | 136 | 120 (80, 120, 80) | 225 | 101% | 9% |
| slot_search | off | 29 | 29 to 29 | 44 | 15 | 12 (12, 12, 12) | 22 | 101% | 8% |
| slot_search | on | 30 | 29 to 30 | 43 | 17 | 12 (12, 12, 12) | 27, then 18 | 102% | 6% |
| browse_servers | off | 183 | 181 to 186 | 322 | 157 | 150 (150, 150, 150) | 300 | 101% | 12% |
| browse_servers | on | 183 | 181 to 186 | 348 | 159 | 150 (150, 150, 150) | 300 | 100% | 12% |

**Reading it:**
- **The twin reaches 55–65% of the laptop's peak.** The laptop's API had 0.75 of an Apple M1 core, and a Graviton2 core is slower per request. The twin also routes every request through Caddy, and runs the worker.
- **The sustained rates are 40–55% of the laptop's.** At the twin's coarser steps (20 req/s for availability), one step is a large share of the result. Availability with the cache on scored 120 req/s on the median step, but 80 in two of three runs, so the cache's capacity gain is again small and uncertain.
- **The same bottleneck as on the laptop:** the API at one full vCPU, Postgres at 6–33%. At saturation the containers added up to 118–143% of the instance's 200%: API 91–102%, Postgres 6–33%, Caddy 2–11%, worker 7–8%, Redis 1–3%.
- **The cache's value is again in Postgres:** 33% → 9% of a vCPU for availability at saturation. The best successful throughput rose 112 → 136 req/s for availability and 15 → 17 for slot search.

### Fixed rate on the twin, cache off vs on

65% of the median uncached rate sustained with no failed request: 52, 7.8, and 48.8 iterations/s for availability, slot search, and browse servers. These are lower than the laptop's rates, so the two sets of latencies aren't directly comparable.

| Script | Rate (iter/s) | Cache | Postgres CPU | API CPU | p50 ms | p95 ms | p99 ms | Errors | Busy conns (mean / max) | p95 range |
|---|---|---|---|---|---|---|---|---|---|---|
| availability | 52 | off | **22%** | 48% | 12.8 | 22.9 | 152 | 0.00% | 0.5 / 8 | 20.8 to 25.2 |
| availability | 52 | on | **5%** | 43% | 9.0 | 15.1 | 60 | 0.00% | 0.3 / 10 | 13.8 to 16.4 |
| | | change | **−75%** | −11% | −30% | −34% | −61% | | | |
| slot_search | 7.8 | off | **6%** | 57% | 67.5 | 320 | 437 | 0.00% | 0.6 / 3 | 309 to 328 |
| slot_search | 7.8 | on | **3%** | 51% | 54.2 | 231 | 396 | 0.00% | 0.5 / 3 | 226 to 241 |
| | | change | **−47%** | −9% | −20% | −28% | −9% | | | |
| browse_servers (control) | 48.8 | off | 9% | 65% | 8.1 | 22.8 | 200 | 0.00% | 0.6 / 10 | 16.0 to 29.3 |
| browse_servers (control) | 48.8 | on | 10% | 66% | 8.2 | 67.2 | 253 | 0.00% | 0.8 / 10 | 16.9 to 121 |

Every measured response was a 200, except 3 × 503 in one cache-on browse-servers run. No iteration was dropped.

**Reading it:**
- **Postgres CPU drops by the same share as on the laptop** (−75% vs −73% for availability).
- **Unlike on the laptop, latency improves clearly** for availability and slot search: the p95 ranges of the three repeats don't overlap. On the slower CPU, the database work the cache removes is a larger share of each request.
- **API CPU fell 9–11% with the cache on here**, where on the laptop it rose 10%. The cost of decoding cached entries is apparently smaller than the cost of the database round trips on this hardware. Faster serialization (see Follow-ups) matters less than the laptop suggested.
- **The browse-servers control** doesn't read availability. Its p95 varied 17–121 ms across repeats with the cache on, the same kind of spread the laptop showed, so its +194% is noise.

### Booking race on the twin

| Phase | Cache | Races | 201 / 409 each | p50 ms | p95 ms | Check |
|---|---|---|---|---|---|---|
| burst | off | 3 | 4 / 96 | 1,188 to 1,289 | 1,641 to 1,751 | pass |
| burst | on | 3 | 4 / 96 | 1,215 to 1,284 | 1,652 to 1,735 | pass |
| keepalive-a | on | 3 | 4 / 96 | 1,185 to 2,323 | 1,627 to 2,803 | pass |
| keepalive-b | on | 3 | 4 / 96 | 1,255 to 1,368 | 1,704 to 1,815 | pass |
| keepalive-c | on | 3 | 4 / 96 | 1,165 to 1,246 | 1,627 to 1,673 | pass |

The races take 2.5 times as long as on the laptop (p50 about 1.2 s vs 0.43–0.52 s), the cost of serialized bookings on a slower CPU. One race in keepalive-a took twice as long as the others; it still had the right winners.

### Cache hit rate on the twin

| Script | Warm-up hits / misses | Warm-up rate | Measured hits / misses | Measured rate |
|---|---|---|---|---|
| availability | 11,338 / 436 | 96.3% | 32,207 / 18 | 99.9% |
| slot_search | 24,144 / 341 | 98.6% | 70,794 / 0 | 100.0% |

The warm-up rate is lower than on the laptop because the fixed rates are lower: about the same number of entries to fill, from fewer requests.

### Keep-alive experiment

The laptop runs had one response with no status (`EOF`) every few slot-search runs, and the explanation was a keep-alive race (see Follow-ups). In production there is a second hop with the same risk. Caddy keeps an idle connection to the API for 2 minutes, but uvicorn closes it after 5 seconds, so Caddy can send a request on a connection that uvicorn is closing. Caddy's HTTP client retries such a request only if it is a GET or wasn't written yet, so a POST, such as slot search or a booking, can come back as 502.

Three arms, each with three slot-search capacity runs (about 1,900 requests each, most of the ramp overloaded), three fixed-rate runs, and three races, cache on:

| Arm | Path | Caddy upstream keep-alive | uvicorn keep-alive | 502 | Status 0 | Peak req/s | Sustained req/s | Fixed-rate p50 / p95 ms | Races |
|---|---|---|---|---|---|---|---|---|---|
| A, production as it is | k6 → Caddy → API | 2 min | 5 s | **1** (run 1, step 9) | 0 | 29 | 12 | 56 / 223 | 3 pass |
| B | k6 → Caddy → API | 60 s | 75 s | **0** | 0 | 29 | 12 | 53 / 222 | 3 pass |
| C, as on the laptop | k6 → API | none | 5 s | none possible | **0** | 29 | 12 | 56 / 212 | 3 pass |

The other phases ran with arm A's settings:

| Phase | 502 on slot search | 502 on availability and browse servers |
|---|---|---|
| burst, 6 slot-search capacity runs | 5, in 4 of the 6 runs (steps 5, 6, 9, 10) | 0 in 12 capacity runs (about 165,000 requests) |
| baseline-2 (0.4 vCPU) | 7 (steps 7 and 9) | 0 |
| fixed-rate runs, races, soak | 0 | 0 |

**Verdict:**
- **The 502s look like the predicted race.** All 13 came from a POST through Caddy with production's timeouts. Not one came from the GET scripts, which Caddy may retry, though their capacity runs sent about ten times as many requests through the same path into the same overload. None came in arm B, where Caddy drops an idle connection before uvicorn does. They appear only in overloaded steps. A likely reason is that under overload Caddy opens one upstream connection per waiting request, hundreds of them, and some sit idle for about 5 s before reuse; at a light load a few connections are reused continuously.
- **But the counts are small.** With production's settings, 5 of 9 runs (burst and arm A) had a 502. If arm B had the same rate, three clean runs would happen by chance about 9% of the time. That is suggestive, not proof.
- **Arm B costs nothing measurable:** the same peak, sustained rate, latency, and race results. Keeping a connection idle for 75 s in uvicorn costs a little memory per connection.
- **So production should use arm B's values,** with Caddy's upstream keep-alive below uvicorn's, as a configuration change: `API_UPSTREAM_KEEPALIVE=60s`, `API_KEEP_ALIVE_SECONDS=75`. That is the usual rule for a proxy (give up on an idle connection before the server does), and these runs show no cost. It is not changed yet.
- **The laptop's status-0 `EOF` did not reproduce.** Arm C took the laptop's path, straight to uvicorn with a 5 s keep-alive, and no request in any phase on the twin ended without a status. The laptop's `EOF` probably came from its own network path (Docker's port forwarding on macOS) rather than from uvicorn, but these runs don't establish that.

### Memory soak

The three read scripts at fixed rates for 30 minutes without restarting anything: browse servers 12 iterations/s (24 req/s), availability 15, and slot search 2. That is about a sixth of each script's clean capacity, about 41 req/s in all, and it kept the API at 42% of a vCPU. Then 10 minutes with no load. Every container's memory was sampled about twice a second.

| Container | First minute MiB | Last minute of load | After 10 min idle | Peak | Slope, second half of load (MiB per 10 min) | Memory limit |
|---|---|---|---|---|---|---|
| api | 187.7 | 192.2 | 192.1 | 205.4 | 2.3 | 448 |
| worker | 96.9 | 99.0 | 99.0 | 178.1 | 0.6 | 256 |
| postgres | 111.8 | 112.0 | 112.2 | 142.2 | 0.2 | |
| caddy | 19.6 | 22.0 | 11.8 | 23.1 | 0.7 | 96 |
| redis | 7.7 | 7.7 | 7.7 | 12.2 | 0.1 | |

73,795 requests: 73,793 × 200 and 2 × 503, p50 9.6 ms, p95 103 ms; 4 iterations dropped.

**Verdict: no sign of a leak in the API.**
- **The API's memory was flat** at 188–190 MiB, as one-minute means, for the first 25 minutes. Then it rose about 3 MiB in two minutes, and stayed flat at 192–193 MiB until the load ended and through the 10 idle minutes. A leak would grow with the requests served: steadily, through the second half. The 2.3 MiB per 10 minutes in the table is that single step, not a trend.
- **The step came with a stall** at minute 26: for about 3 s every pool connection was busy and the API's CPU read above 100%. The two 503s most likely came from it, though k6 doesn't record when each request ended. The cause isn't visible in these samples.
- **An upper bound:** the whole 30 minutes added 4–5 MiB over 73,795 requests. So if these read paths leak at all, it is under about 60 bytes per request.
- **Production's growth isn't explained by this.** Production's API went from 208 MiB after a deploy to 318 MiB after a day that included assistant use. The soak exercised only the read paths: no assistant, logins, refreshes, or uploads. Something in those paths, or a one-off rise such as loading the assistant's clients, could account for it; this run can't tell which.
- **Postgres, Redis, and Caddy** stayed level. Caddy released about half its memory after the load stopped.
- **The worker's peaks are its health check, not a leak.** Its memory jumped from about 100 to 176–178 MiB for about 2 s, 74 times in 40 minutes, every 32–33 s: the health check's 30 s interval plus the check itself. The worker averaged 8% of a vCPU under load and when idle alike, though its cron jobs take 10–50 ms. That was the old check, `arq --check`, which starts a Python process that imports the whole app. It took the worker to 70% of its 256 MiB limit every half minute. See the next finding.

### Simulated baseline

**These numbers are simulated.** A t4g.small without CPU credits runs at 20% of each vCPU, 0.4 of a vCPU in all. Instead of draining the credits, which takes hours, every container ran in one systemd slice capped at 40% of a CPU (`loadtest/compose.baseline.yml`). The OS and Docker are outside the cap, so the figures are slightly generous. Capacity only, cache on, one repeat, with ceilings of 80, 60, and 12 iterations/s.

| Script | Peak req/s | Best successful req/s | Sustained req/s (p95 ≤ 500 ms) | Burst best successful, cache on | Share of burst |
|---|---|---|---|---|---|
| availability | 70 | 33 | 16 | 136 | 24% |
| slot_search | 12 | 5.6 | 2.4 | 17 | 32% |
| browse_servers | 88 | 38 | 24 | 159 | 24% |

**Reading it:**
- **The peak column is misleading here:** past the knee, more than half of the completed requests were 503s. Successful throughput is the figure to use.
- **A credit-exhausted instance keeps a quarter to a third of its burst capacity,** a little more than the 20% CPU share would suggest. The sustained rates are low, though: 16 availability requests per second at p95 ≤ 500 ms, and slot search already had a p95 of 379 ms at 1.2 req/s.
- **The collapse past saturation is sharper than in burst:** availability went from 33 successful req/s down to 10–25, and browse servers from 38 to 14–24, with p95 at 4–8 s. k6 reached its 300-VU limit and dropped iterations in the overloaded steps.
- **The API used 28–30% of a vCPU at saturation,** Caddy 1–4%, Postgres 3–5%, and the worker 2–3% with the new health check. Everything shared the 40%, so a second uvicorn process would gain nothing in this state.
- **7 × 502 on slot search,** in steps 7 and 9, with production's keep-alive settings (see above).

### Finding: one API process uses one of two vCPUs

In every burst capacity run the API sat at 91–102% of a vCPU, the most a single uvicorn process can use, while the instance as a whole used 118–143% of 200%. Postgres (6–33%) and Caddy (2–11%) had headroom, and so did the instance. The first trial runs showed the same thing (API 95–100%, Postgres 5–37%, Caddy under 15%).

**What to evaluate:** two uvicorn workers. Against it:
- **Memory.** One API process used 188–205 MiB in these runs and 219–318 MiB in production, against the API container's 448 MiB limit. Two would need about 400–650 MiB. That means raising the limit within the instance's 2 GiB, which also holds Postgres, the worker, Redis, and Caddy, or making each process smaller.
- **A smaller gain than 2×.** Postgres and Caddy already use 11–43% of a vCPU at the API's saturation, and they would use more with two processes.
- **Nothing without credits.** In the simulated baseline every container shared 0.4 of a vCPU, so a second process would only compete for it. The gain applies only while the instance has credits.
- **Two pools.** Each process would have its own pool of 5 + 5 connections. Twice the connections is still well within Postgres's limit, but it also doubles the work Postgres can be asked to do at once, which is where the cache helps (see the laptop summary).

Not changed in this round.

### Finding: throughput collapses on the pool timeout

Successful throughput per step, burst phase, cache off, medians of three repeats:

| Offered req/s | availability: all / successful | availability p95 ms | browse_servers offered | browse_servers: all / successful | browse_servers p95 ms |
|---|---|---|---|---|---|
| 80 | 80 / 80 | 26 | 120 | 120 / 120 | 124 |
| 100 | 99 / 99 | 1,063 | 150 | 150 / 150 | 126 |
| 120 | 115 / **112** | 2,273 | 180 | 160 / **157** | 1,841 |
| 140 | 131 / 98 | 3,135 | 210 | 172 / 140 | 3,052 |
| 160 | 146 / 81 | 3,324 | 240 | 182 / 134 | 3,057 |
| 180 | 152 / 82 | 3,209 | 270 | 180 / 127 | 3,058 |
| 200 | 149 / 93 | 3,206 | 300 | 179 / 138 | 3,052 |

Past the best step, the API completes about as many requests as before, but up to 45% of them are 503s after waiting the pool's full 3 s checkout timeout (`database_pool_timeout_seconds`, pool 5 + 5). The successful throughput falls by 11–28%. Every failure in the burst capacity runs was a 503, apart from the keep-alive 502s above. The first trial on the twin showed the same at its first overloaded step: p50 about 1 s, with 20% failing as 503.

**Why:** the API's CPU is the limit, not the pool. Requests pile up waiting for a connection while the event loop is busy. Each one that ends in a 503 has spent CPU on the way in (parsing, authentication, routing) and on the error response, all taken from requests that could have succeeded. And a request that does get a connection has often waited so long that its client sees seconds of latency anyway. So the 503 with `Retry-After` protects the database, which was the intent, but it doesn't protect the API's CPU.

**What would degrade better:** shedding load before the expensive part, quickly. Options include a much shorter checkout timeout for reads, a cap on requests in flight that answers 503 immediately once reached (uvicorn's `--limit-concurrency`, or a small middleware), or rate limits per client in Caddy. The aim is that successful throughput stays at its best step while the extra requests fail in milliseconds. Each option needs its own run: a cap set too low would turn away load the API could serve. Not changed in this round.

### Finding: the worker's health check needed a full CPU

The first baseline run (`20261113T001352Z-baseline`) failed before sending any load: `docker compose up -d --wait` reported the worker unhealthy. The worker itself was fine. Its cron jobs ran in 10–50 ms, but every health check took longer than its 5 s timeout, even with nothing else happening. The check was `arq --check`, which starts a new Python process and imports the whole app before reading one Redis key. At full CPU that costs a fraction of a second; with 0.4 of a vCPU shared with four other containers, more than 5 s. The soak above shows its cost even at full CPU: an extra 75 MiB in the worker, and about 8% of a vCPU, every 32 s.

**Why it matters for production:** a production instance that has spent its credits behaves like this one. Its worker would be marked unhealthy, and a deploy, which waits for healthy containers, would fail.

**Fix:** the check now reads arq's heartbeat key directly with the Redis client (`exists("arq:queue:health-check")`) instead of importing the app, in both Compose files, with an integration test against a real Redis (`3aab152`). baseline-2 ran with it: the check passed every time, but under load at 0.4 of a vCPU it took 4.5–4.9 s, about 2 s once the load stopped. Most of that is starting Python on a starved CPU, so the timeout is now 10 s (`2b2ddac`). Both are in production since 2026-11-13, where the check takes 0.77 s at full CPU. The failed run is kept as the record. Reasoning: [docs/decisions.md, "Worker health check without the app import"](../docs/decisions.md).

## Follow-ups

- **Profile slot search in the API.** Its time is spent in the API process, not in Postgres: 30% of a core at 5.9 req/s with the cache off or on, and 70–77% (at the 75% limit) at 28 req/s. Decoding many day entries per server and computing free windows are the likely costs. A profile would show where the time goes.
- **Status-0 `EOF` under load.** *Closed by the twin runs.* On the laptop, one slot-search request every few runs got no response, and the suspected cause was uvicorn's 5 s keep-alive. On the twin, k6 straight to uvicorn with the same 5 s produced no status-0 response in any run (arm C), so the laptop's `EOF` was probably its own network path. The keep-alive race between Caddy and uvicorn is real, though: see [Keep-alive experiment](#keep-alive-experiment).
- **Faster cache serialization.** Cached entries are JSON encoded and decoded with the standard library, and that cost shows on the laptop: availability's API CPU is 10% higher with the cache on. On the twin it was 11% lower, so this matters less than it first seemed. orjson or msgpack would cut it. Either is a new dependency, so it needs its own decision, and it should come after the profile confirms that decoding is where the time goes.
- **Run on the target instance.** *Done:* see [On a twin of the production instance](#on-a-twin-of-the-production-instance).
- **Keep-alive settings in production.** Set `API_UPSTREAM_KEEPALIVE=60s` and `API_KEEP_ALIVE_SECONDS=75`, so Caddy drops an idle connection to the API before uvicorn does (arm B above).
- **Two uvicorn workers.** Evaluate against the API's 448 MiB memory limit and the instance's 2 GiB; see [the finding](#finding-one-api-process-uses-one-of-two-vcpus).
- **Fast load shedding.** Reject requests beyond what the API can serve in milliseconds instead of after the 3 s pool timeout, and rerun capacity to check that successful throughput stays at its best step; see [the finding](#finding-throughput-collapses-on-the-pool-timeout).
- **A soak that includes the assistant.** The read-path soak found no leak, so production's growth from 208 to 318 MiB over a day with assistant use is still unexplained. A soak with the scripted model, logins, and refreshes would show whether those paths grow.
