---
title: Booking rules
last_reviewed: 2026-11-08
---

# Booking rules

## Time grid

All bookings use a half-hour grid. The grid keeps the calendar simple and makes sure the time freed by one booking can be used by the next.

### Start and end times

Bookings start and end on the hour or the half hour in the lab's local time, for example 14:00 or 14:30. A start at 14:15 or an end at 16:45 is refused with an `invalid-time-grid` error. The end must be after the start; otherwise the error is `invalid-time-range`.

### Minimum and maximum length

The shortest booking is 30 minutes. The longest is the server's maximum booking length, which is 12 hours unless an administrator set a different value for that server. The server page shows the limit. A longer booking is refused with a `booking-too-long` error.

For a job that needs more time than one booking allows, book consecutive slots or use checkpoints so the job can resume in a later booking. Each booking still counts toward your weekly quota and the limit on active reservations.

## How far ahead you can book

The start of a booking must be in the future and at most 14 days from now. A start further out is refused with `start-too-far`, and a start that has already passed is refused with `start-in-past`. The limit applies to the start only, so a booking that starts in 14 days may end a few hours after that.

You cannot reserve a server for next month yet. Come back when the date is within 14 days.

## How many reservations you can hold

You can hold at most 4 active reservations at a time. A fifth booking is refused with a `too-many-reservations` error until one of the four ends or is cancelled.

### What counts as active

A reservation is active while it is pending or approved and has not yet ended. That includes:

- bookings that are running right now;
- approved bookings that have not started;
- pending requests waiting for an administrator, including one whose start has just passed, until the system marks it expired a minute later.

Completed, cancelled, rejected, and expired reservations do not count.

## GPU count per booking

A booking asks for a number of GPUs on one server, from 1 up to the number of GPUs that server has. Asking for more than the server has is refused with `too-many-gpus`. The system assigns the specific GPUs, starting with the lowest free GPU numbers, and shows them on the reservation.

A booking cannot span several servers. For a job that needs more GPUs than one server has, book on the largest server that fits, or contact the lab administrators.

## Which servers can be booked

You can book servers whose status is available and whose lab is active. Some servers need an administrator's approval; see the approval policy.

### Maintenance and retired servers

A server in maintenance is shown in the server list but cannot be booked until it is available again. Retired servers are hidden from students and cannot be booked. A booking on a server that cannot be booked is refused with a `server-unavailable` error.

### Inactive labs

When a lab is deactivated, its servers cannot be booked and are hidden from the server list. Reservations you already hold there stay visible in My reservations and can still be cancelled.

## When a booking is refused

The booking dialog shows the reason next to the field it concerns, or above the form. The error types below are what the system reports.

### Errors about your request

These depend only on what you asked for and the server's settings. Changing the request fixes them.

- `invalid-time-grid`: the start or end is not on the hour or half hour.
- `invalid-time-range`: the end is not after the start.
- `start-in-past`: the start has already passed.
- `start-too-far`: the start is more than 14 days ahead.
- `booking-too-long`: the booking is longer than the server allows.
- `too-many-gpus`: more GPUs than the server has.
- `peak-hours-limit`: the booking uses more than 4 GPU-hours inside weekday peak hours.

### Errors about other bookings or server state

These depend on other bookings or on the server's current state, so the same request may succeed at another time or later.

- `gpu-unavailable`: not enough GPUs are free on that server for that time. Try another time or server, or use "Find a slot".
- `allocation-contention`: many people tried to book the same GPUs at the same moment. Try again.
- `quota-exceeded`: the booking would take you over your weekly quota.
- `too-many-reservations`: you already hold 4 active reservations.
- `server-unavailable`: the server is in maintenance or retired, or its lab is inactive.
