---
title: Data and storage
last_reviewed: 2026-11-08
---

# Data and storage

## Local scratch storage

This page is lab policy. The booking system does not manage files on the servers; these rules are what every user agrees to follow.

Each server has fast local disk space, mounted as scratch, for datasets, checkpoints, and outputs while you work. Scratch space is shared by everyone who books that server. Keep your files in a directory named after your account and clean up after your booking.

### Scratch is not backed up

Nothing on scratch is backed up. A disk failure, a server reinstall during maintenance, or a hardware fault in the middle of your booking can lose everything on the local disk. Files older than 14 days may be deleted without notice to keep space free.

Write checkpoints regularly and copy anything you want to keep to your group's own storage.

## Moving data on and off servers

Copy large datasets to scratch at the start of your booking with `rsync` or your group's object storage tools, rather than reading them over the network during training. Compress many small files into archives before copying. Transfers count against your booked time, so start them early.

## Sensitive and regulated data

Do not store or process sensitive or regulated data on the lab servers. That includes health records and patient data, personal data about identifiable people, student records, data under a data use agreement that requires controlled environments, and export-controlled material. The lab servers are shared machines with no access controls between users on scratch.

If your project involves such data, contact the lab administrators before you start. They can point you to university systems approved for it.

## Datasets and licenses

You are responsible for having the right to use the datasets and model weights you bring onto the servers. Respect their licenses, including any non-commercial or no-redistribution terms. Do not leave licensed datasets in shared locations where other users can copy them.

## Before your booking ends

Your access ends at the booking's end time, and the next person may start immediately. Before the end:

- stop your processes and save a final checkpoint;
- copy results and checkpoints off the server;
- delete large temporary files from scratch.

If your job finishes early, end the booking early so the remaining time is released.
