---
title: Cancellation and early release
last_reviewed: 2026-11-08
---

# Cancellation and early release

## Cancelling before the start

Cancel from My reservations. Cancelling releases the GPUs immediately so other people can book them, and returns all of the booking's hours to your weekly quota.

### Pending requests

A pending request can be cancelled at any time while it is still pending, including after its start time has passed but before the system marks it expired. Its status becomes cancelled.

### Approved bookings

An approved booking can be cancelled at any time up to its start time, including the moment it would start. Its status becomes cancelled and all of its hours are returned. There is no penalty and no notice period; please cancel as soon as you know you will not use the time.

## Ending a running booking early

Once a booking has started, cancelling it ends it early instead. Use this when your job finishes or crashes before the booked end, so the remaining time goes back to everyone.

### Where the booking ends

The booking is shortened to the next half-hour boundary after the moment you end it, because new bookings can only start on the half hour. If you end a booking at 14:10, it ends at 14:30. In the last half hour of a booking, the end time stays where it was. The status becomes completed, not cancelled, because the booking did run.

### Effect on your quota

Only the time up to the new end counts toward your weekly quota. The hours after it are returned immediately. For example, ending a 2-GPU booking from 13:00 to 19:00 at 14:10 shortens it to 13:00–14:30, so it uses 3 GPU-hours instead of 12.

## Bookings that cannot be cancelled

A booking that has already ended cannot be cancelled or changed, and neither can a reservation that is already completed, cancelled, rejected, or expired. Trying returns an `invalid-reservation-state` error. Its hours stay counted in the week it ran, if it ran.

## Cancellations by admins

Lab administrators can cancel any reservation, for example when a server needs urgent repair, when a booking breaks lab policy, or when an account is deactivated. A running booking cancelled by an administrator ends early in the same way as above. You receive an email whenever an administrator cancels or ends your booking.

If an administrator cancels a booking and you think it was a mistake, contact the lab administrators.

## Email notifications

You receive an email when someone else changes your reservation: when an administrator approves, rejects, or cancels it, and when a pending request expires. You also receive a reminder an hour before an approved booking starts. You do not receive an email when you cancel or end your own booking yourself, because you just did it.
