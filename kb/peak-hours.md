---
title: Peak hours
last_reviewed: 2026-11-08
---

# Peak hours

## What counts as peak time

Peak time is 09:00 to 18:00 on weekdays, in the local time of the lab that owns the server. These are the hours when most people are on campus and demand for GPUs is highest.

### Weekdays only

Peak time applies Monday through Friday. Saturdays and Sundays have no peak window, so the peak limit does not apply to weekend bookings at all, at any hour. University holidays that fall on a weekday are still treated as weekdays.

### Lab local time

The peak window follows the clock of the server's lab, not your own time zone. A booking on a server in a lab on Eastern time is checked against 09:00 to 18:00 Eastern, even if you book from the West Coast. The server page and the booking dialog show times in the lab's zone with its abbreviation, so the times you see are the times the rule uses.

## The per-booking peak limit

A single booking may use at most 4 GPU-hours inside the peak window. Time outside the window is not limited by this rule; only the weekly quota and the server's maximum booking length apply there.

### How GPU-hours are calculated

Multiply the number of GPUs in the booking by the hours of the booking that fall between 09:00 and 18:00 on a weekday. If the result is more than 4, the booking is refused with a `peak-hours-limit` error that says how many peak GPU-hours it would use.

Only the overlap counts. A booking from 16:00 to 22:00 has 2 peak hours, from 16:00 to 18:00.

### Examples

- 1 GPU from 10:00 to 14:00 on a Tuesday: 1 × 4 = 4 GPU-hours. Allowed.
- 2 GPUs from 13:00 to 15:00 on a Thursday: 2 × 2 = 4 GPU-hours. Allowed.
- 3 GPUs from 10:00 to 12:00 on a Wednesday: 3 × 2 = 6 GPU-hours. Refused. Two GPUs for the same two hours, or three GPUs for one hour, would be allowed.
- 4 GPUs from 17:00 to 23:00 on a Monday: only 17:00 to 18:00 is peak, so 4 × 1 = 4 GPU-hours. Allowed.
- 8 GPUs from 08:30 to 09:30 on a Friday: 30 minutes are peak, so 8 × 0.5 = 4 GPU-hours. Allowed.
- 8 GPUs from 10:00 to 18:00 on a Saturday: no peak time on weekends. Allowed, subject to your weekly quota.

### Bookings that span more than one peak window

The limit is per booking, not per day. If a booking touches two peak windows, for example from 15:00 on Monday to 10:00 on Tuesday, the peak time of both days is added together: 3 hours on Monday and 1 hour on Tuesday make 4 peak hours, so this booking is allowed with 1 GPU and refused with 2.

This only matters for bookings longer than 15 hours, which are possible only on servers whose maximum booking length was raised above the default.

## Planning around peak hours

Large jobs fit best in the evening, overnight, or on weekends, where the peak limit does not apply. Some ways to plan:

- Start multi-GPU runs at 18:00 or later, or end them by 09:00.
- Use daytime bookings for debugging and short experiments on one or two GPUs. One GPU for up to 4 hours, or two GPUs for up to 2 hours, fits inside the limit.
- Use "Find a slot" with your GPU count and duration. It only suggests times that pass the peak limit and your quota.

## Why the limit exists

During the working day many people need a GPU for a short time: debugging, a quick evaluation, a class assignment. The peak limit keeps one large booking from taking a whole server during those hours, so short daytime jobs can always find room. It does not reduce the total time you can book, because the hours outside the window are unlimited apart from your weekly quota.
