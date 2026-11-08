---
title: Fair use and weekly GPU-hour quota
last_reviewed: 2026-11-08
---

# Fair use and weekly GPU-hour quota

## Why there is a quota

The lab GPUs are shared by every research group in the department, and demand is highest in the weeks before conference deadlines. Without a limit, a few long-running projects could hold most of the hardware for weeks at a time. The weekly quota gives every person a predictable share of GPU time while still allowing large jobs to be split across several bookings.

The quota measures GPU-hours, not bookings or servers. One GPU for one hour is one GPU-hour, so a booking of 4 GPUs for 3 hours uses 12 GPU-hours.

## Your weekly quota

### Default allowance

Every new account starts with a quota of 48 GPU-hours per week. That is enough for, for example, two overnight runs of 12 hours on 2 GPUs each, or one 8-GPU run of 6 hours.

The quota is a hard limit. A booking that would take you over it in any week is refused with a `quota-exceeded` error, and the message says which week overflowed and how many hours you have already used there.

### Changing your quota

Lab administrators can set a different weekly quota for an individual account, higher or lower. A change applies to new bookings immediately; bookings you already hold are not cancelled if your quota is lowered.

There is no automatic increase and no way to change your own quota in the app. To ask for more hours, see "Requesting a quota change" in the contact and escalation guide. An increase stays in place until an administrator changes it again.

## When the week starts and ends

The quota week runs from Monday 00:00 to the following Monday 00:00 in Pacific time (America/Los_Angeles). Usage is added up across all labs, so hours booked in different labs count against the same weekly total.

### Labs in other time zones

The quota week is the same for every lab and every user: it always resets at Monday 00:00 Pacific time, wherever the lab is and wherever you are. For a lab on Eastern time, the week therefore starts at 03:00 on Monday local time. Your own location and your browser's time zone make no difference.

This is different from peak hours, which follow each lab's local clock. In a week with a daylight saving change, the quota week is 167 or 169 hours long instead of 168.

### Bookings that cross Monday

A booking that runs across the Monday 00:00 boundary is split between the two weeks. Each week is charged only for the part of the booking that falls inside it. For example, 2 GPUs from Sunday 20:00 to Monday 04:00 Pacific time charges 8 GPU-hours to the earlier week and 8 to the later one.

Both weeks must have room. If the later week is already full, the booking is refused even though the earlier week has hours left.

## How usage is counted

### Which reservations count

Usage includes every reservation that is pending, approved, or completed. Pending requests count from the moment you submit them, even while they wait for an administrator, because they already hold their GPUs.

Rejected, cancelled, and expired reservations do not count, so their hours are free again as soon as the status changes.

### Partial overlaps

Usage is the number of GPUs multiplied by the hours of the booking that fall inside the week. Hours outside the week are not charged to it. Bookings are made in half-hour steps, so usage can be a fractional number such as 13.5 GPU-hours.

## Getting hours back

You get quota back when a reservation stops counting or gets shorter:

- Cancelling a pending or approved reservation before it starts returns all of its hours.
- A rejected or expired request returns all of its hours.
- Ending a running booking early returns the hours after the new end time. The booking is shortened to the next half-hour boundary, and only the time up to then is charged.

## Checking your remaining quota

The dashboard shows a quota bar with the hours used and remaining in the current week and the dates of the week. The booking dialog shows the remaining hours for the week of the start you pick, so a booking for next week is checked against next week's numbers.
