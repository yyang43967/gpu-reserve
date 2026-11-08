---
title: Approval for restricted servers
last_reviewed: 2026-11-08
---

# Approval for restricted servers

## Which servers need approval

Some servers are restricted: a lab administrator must approve each booking before it is confirmed. Restricted servers are marked as requiring approval on their server page and in the server list. Today these are the H100 servers, which are reserved mainly for large training runs that cannot fit on other hardware.

All other servers confirm bookings immediately, as long as the booking passes the usual rules.

## What happens after you request

A booking on a restricted server is created with the status pending. It goes into the administrators' approval queue, and you can see it in My reservations. An administrator then approves or rejects it. You receive an email either way.

### Pending requests hold GPUs

While your request is pending, its GPUs are held for you. Nobody else can book those GPUs for that time, and the request counts toward your weekly quota and your active reservations. If you no longer need the time, cancel the request so the GPUs are released.

### Decision deadline

Administrators can approve a request only before its start time. A request that is still pending when its start time arrives expires automatically within about a minute: its status becomes expired, the GPUs are released, the hours return to your quota, and you receive an email. An expired request cannot be revived; submit a new booking for a later time.

Requests made far in advance are easier to decide in time. For a request close to its start, it can help to tell the lab administrators.

## Writing a good purpose

Administrators decide from the purpose you enter with the booking, up to 500 characters. A good purpose explains why the job needs this hardware:

- the model and its size, and the kind of job (pretraining, full fine-tuning, large-scale evaluation);
- why smaller GPUs are not enough, for example the memory needed per GPU or the need for fast GPU-to-GPU links;
- the project or paper and any deadline;
- whether the run can resume from checkpoints if it is interrupted.

"Pretraining a 1.3B model for the X project, needs 8 × 80 GB with NVLink for FSDP; checkpoints every hour; deadline Dec 1" is far more likely to be approved than "training".

## Rejections

An administrator who rejects a request must give a reason, which is shown on the reservation and included in the email. A rejected request releases its GPUs and its hours immediately. Common reasons are a job that would fit on unrestricted hardware, a purpose that does not explain the need, or a conflict with a scheduled large run.

You can submit a new request with a better explanation or for another time. If you believe a rejection is mistaken, see "Disputes about approvals" in the contact and escalation guide.

## Approved bookings

Once approved, a booking behaves like any other confirmed booking: it starts and ends at the booked times, you get a reminder an hour before the start, and you can cancel it up to the start or end it early while it runs. An administrator can still cancel an approved booking, for example for urgent maintenance, and you receive an email if they do.
