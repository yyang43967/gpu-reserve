---
title: Reservation lifecycle
last_reviewed: 2026-11-08
---

# Reservation lifecycle

## Statuses

Every reservation has one status. My reservations shows it next to each booking.

### Pending

Waiting for an administrator's decision on a restricted server. A pending request holds its GPUs and counts toward your quota and active reservations. It becomes approved, rejected, cancelled, or expired.

### Approved

Confirmed. Bookings on unrestricted servers are approved as soon as they are made. An approved booking becomes completed when it ends, or cancelled if it is cancelled before it starts.

### Running

Running is not a separate stored status. It is the label shown for an approved booking between its start and end time. A pending request whose start has passed is not running: it never got its GPUs and will expire.

### Completed

The booking ran. It either reached its end time or was ended early. Completed bookings count toward the quota for the time they ran.

### Rejected, cancelled, and expired

These reservations never ran and do not count toward your quota.

- Rejected: an administrator declined the request and gave a reason.
- Cancelled: you or an administrator cancelled it before it started.
- Expired: a pending request was not decided before its start time.

## Automatic transitions

Two background jobs move reservations along without anyone acting on them.

### Expiry of undecided requests

Every minute, pending requests whose start time has passed are marked expired. Their GPUs are released and their hours return to the owner's quota, and the owner receives an email. This is why a request on a restricted server can disappear from your upcoming list even though you never cancelled it: it was not approved before it was due to start.

### Completion at the end time

Every five minutes, approved bookings whose end time has passed are marked completed. No email is sent for this. Until the job runs, a booking that just ended can still show as approved for a few minutes.

## Reminders

You receive an email reminder about an hour before each approved booking starts, with the server, the GPUs, and the times in the lab's time zone. Reminders are checked every five minutes, so a booking made less than an hour before its start gets its reminder within a few minutes of booking. No reminder is sent for a booking that was cancelled or is still pending.

## Calendar files

"Add to calendar" on a reservation downloads an iCalendar (.ics) file that you can import into Google Calendar, Outlook, or Apple Calendar. Only the owner of a reservation can download it. A pending request appears as a tentative event.

### Updating a calendar after a change

Each reservation keeps the same calendar event ID across downloads. After a reservation is rejected, cancelled, or expires, download its calendar file again and import it: the new file marks the event as cancelled with a higher version number, so your calendar app removes the event or strikes it out instead of keeping a booking that no longer exists. After a running booking is ended early, the new file keeps the event with its shortened end time.
