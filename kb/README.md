# Knowledge base

Policy documents for the assistant's `search_policies` tool. Each file has front matter (`title`, `last_reviewed`) and H2/H3 sections. A section is identified by its heading path (`H2` or `H2 › H3`), which is what retrieval cites and what the labeled questions in `evals/rag/` point at. Heading paths must be unique within a document.

Load them with `python -m app.rag.ingest` from `backend/`. Ingestion is idempotent: unchanged chunks are skipped, and chunks of edited or deleted sections are removed.

## Rules and where they are enforced

Every rule the app enforces is stated in exactly the terms the code uses. `backend/tests/test_kb_consistency.py` checks the numbers against the constants, and checks that every error type named in the documents exists.

| Rule | Enforced in | Stated in |
|---|---|---|
| 48 GPU-hours per week by default, per user | `models/user.py` (`DEFAULT_WEEKLY_GPU_HOUR_QUOTA`), `booking_rules.check_quota` | fair-use-and-quota › Your weekly quota › Default allowance |
| Quota week Monday 00:00 to Monday 00:00 in one zone for all labs | `booking_rules.week_containing`, `QUOTA_TIMEZONE` | fair-use-and-quota › When the week starts and ends |
| Every week a booking overlaps must have room | `booking_rules.check_quota` | fair-use-and-quota › … › Bookings that cross Monday |
| Pending, approved, and completed count; only the overlapping part | `models/reservation.py` (`QUOTA_STATUSES`), `booking_rules.used_gpu_minutes` | fair-use-and-quota › How usage is counted |
| Peak window weekdays 09:00–18:00 lab time | `booking_rules.peak_minutes` | peak-hours › What counts as peak time |
| At most 4 peak GPU-hours per booking, summed over windows | `booking_rules.check_peak_hours` | peak-hours › The per-booking peak limit |
| 30-minute grid, minimum 30 minutes | `booking_rules.check_times` | booking-rules › Time grid |
| Maximum length per server, default 12 hours | `models/server.py` (`DEFAULT_MAX_BOOKING_HOURS`), `check_times` | booking-rules › Time grid › Minimum and maximum length |
| Start in the future, at most 14 days ahead | `check_times` | booking-rules › How far ahead you can book |
| At most 4 active reservations, running and pending included | `reservations.py` (`MAX_ACTIVE_RESERVATIONS`) | booking-rules › How many reservations you can hold |
| GPU count up to the server's count; lowest free GPUs assigned | `check_gpu_count`, allocation in `reservations.py` | booking-rules › GPU count per booking |
| Only available servers in active labs can be booked or approved | `reservations.py` (server and lab checks, approval) | booking-rules › Which servers can be booked; maintenance-windows › New bookings |
| Restricted servers start pending; approval only before the start | `create_reservation`, `approve_reservation` | approval-policy |
| Rejection requires a reason | `schemas/reservation.py` (`DecisionReason`) | approval-policy › Rejections |
| Pending past start expires every minute | `jobs.expire_pending` | reservation-lifecycle › Automatic transitions; approval-policy › … › Decision deadline |
| Approved past end completes every five minutes | `jobs.complete_finished` | reservation-lifecycle › Automatic transitions |
| Cancel pending any time, approved until the start; running ends at the next half hour as completed | `reservations.finish_reservation` | cancellation-and-early-release |
| Who is emailed | `notifications.notify_owner`, `jobs.queue_reminders` | cancellation-and-early-release › Email notifications; reservation-lifecycle › Reminders |
| Calendar file: owner only, cancelled events, same UID, higher sequence | `services/calendar.py` | reservation-lifecycle › Calendar files |
| Maintenance does not cancel existing bookings; no automatic email | `servers.py` (status change only) | maintenance-windows › Existing bookings |
| Passwords 8–128 characters; sign-in and password-change limits | `core/security.py`, `schemas/auth.py`, `rate_limit.py` | security-and-accounts › Passwords |
| Deactivation releases bookings and emails the owner | `users.py` deactivation | security-and-accounts › Account deactivation |
| GPU memory per model | `models/server.py` (`GPU_MEMORY_GB`) | hardware-guide › GPU models |

Policies that the app does not enforce (data handling, maintenance schedule, acknowledgments, contacts, response times) are labeled as policy and make no claims about what the software does.

## No live inventory

The documents do not list servers, hostnames, or GPU counts. Those change as hardware is added and retired; the assistant gets them from the server list instead.
