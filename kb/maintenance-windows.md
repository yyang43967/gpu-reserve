---
title: Maintenance windows
last_reviewed: 2026-11-08
---

# Maintenance windows

## Planned maintenance

Servers need regular work: operating system and driver updates, firmware upgrades, disk replacements, and cleaning. Planned work is scheduled in advance so that it affects as few bookings as possible.

### Schedule

Routine maintenance takes place on the first Tuesday of each month between 06:00 and 10:00 lab time, one lab at a time. Larger upgrades are announced at least one week ahead. Avoid booking across a planned window on the affected servers; the administrators may cancel bookings that overlap it.

## What maintenance means for bookings

Before work starts, an administrator switches the server's status to maintenance. The server stays visible in the server list with a maintenance label.

### New bookings

A server in maintenance cannot be booked. Booking attempts are refused with a `server-unavailable` error until the server is available again. Pending requests on it cannot be approved during maintenance either.

### Existing bookings

Switching a server to maintenance does not cancel existing bookings by itself. Your reservation stays in My reservations with the same GPUs and times. If the work will overlap your booking, an administrator will contact you and may cancel the booking, in which case you receive the cancellation email. If the server is available again before your booking starts, nothing changes for you.

If you are unsure whether your booking will be affected, contact the lab administrators.

## Unplanned outages

Hardware faults, power problems, and cooling failures can take a server down without warning. The administrators switch the affected server to maintenance as soon as they know. Repairs that need replacement parts can take days. If an outage interrupts your running booking, end the booking early to get the unused hours back, and report the fault (see "Hardware faults during a booking" in the contact and escalation guide).

## Finding out about maintenance

The server list and server pages show the current status of every server. Planned windows and larger upgrades are announced on the lab mailing list. The booking system does not send automatic emails when a server goes into maintenance; the administrators contact the owners of affected bookings directly.
