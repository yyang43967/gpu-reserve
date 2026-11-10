# Hand labels

36 answers, 60 verdicts. For each verdict line, write `pass` or `fail` after the colon, using the criteria in `rubric.md`. Then run `python -m app.agent.evaluation label-read`.

The statement list under each answer is mechanical: sentences with [n] show the sections they cite; other sentences show the retrieved section sharing the most words, or 'no support found'. It is a reading aid, not a verdict, and it does not know which statements are about rules, policies, or hardware.

## Answer 0f754ef70df0

Case: `oos-shopping-01`

**User**

> Recommend a laptop for machine learning under $2000.

**Retrieved sections**

(none)

**Reference facts**

- The request is outside GPU booking and the lab's policies; the assistant declines and says what it can help with.

**Final answer**

> I can't recommend laptops. I only help with this lab's GPU servers: finding hardware that fits a job, checking availability, booking, and managing your own reservations and quota.
>
> If you need GPU capacity for machine learning, I can search the lab's servers by GPU model, memory, or count, or find a free slot for your job. Tell me roughly what you're training, such as the model size and whether it's LoRA or full fine-tuning, and I'll check the lab's hardware guidance.

**Statements and support**

- “I can't recommend laptops.” — not cited; no support found
- “I only help with this lab's GPU servers: finding hardware that fits a job, checking availability, booking, and managing your own reservations and quota.” — not cited; no support found
- “If you need GPU capacity for machine learning, I can search the lab's servers by GPU model, memory, or count, or find a free slot for your job.” — not cited; no support found
- “Tell me roughly what you're training, such as the model size and whether it's LoRA or full fine-tuning, and I'll check the lab's hardware guidance.” — not cited; no support found

**Your verdicts**

- verdict declines:

## Answer 1315c73f1c43

Case: `oos-code-01`

**User**

> Write a Python function that sorts a list of dictionaries by a key.

**Retrieved sections**

(none)

**Reference facts**

- The request is outside GPU booking and the lab's policies; the assistant declines and says what it can help with.

**Final answer**

> I can't help with general programming questions. I'm the GPU Reserve assistant, so I only cover this lab's GPU booking platform: policies, server availability, quota, and your own reservations.
>
> If you need GPU time for a job, tell me what the job involves (model size, GPU memory needs, number of GPUs, and how long it will run), and I can find suitable servers and open slots.

**Statements and support**

- “I can't help with general programming questions.” — not cited; no support found
- “I'm the GPU Reserve assistant, so I only cover this lab's GPU booking platform: policies, server availability, quota, and your own reservations.” — not cited; no support found
- “If you need GPU time for a job, tell me what the job involves (model size, GPU memory needs, number of GPUs, and how long it will run), and I can find suitable servers and open slots.” — not cited; no support found

**Your verdicts**

- verdict declines:

## Answer 1345f2f2a5dc

Case: `policy-peak-01`

**User**

> Can I book 3 GPUs from 10 to 12 on a weekday?

**Retrieved sections**

(none)

**Reference facts**

- A single booking may use at most 4 GPU-hours inside peak time (09:00-18:00 on weekdays, lab time).
- 3 GPUs for 2 peak hours is 6 GPU-hours, so it is refused.

**Final answer**

> I can check that, but I need the exact weekday or date.
>
> - Today is Tuesday, 2026-11-10 in the lab time zone.
> - If you mean a specific weekday, tell me which one and I’ll check whether 3 GPUs from 10:00 to 12:00 is available.

**Statements and support**

- “I can check that, but I need the exact weekday or date.” — not cited; no support found
- “Today is Tuesday, 2026-11-10 in the lab time zone.” — not cited; no support found
- “If you mean a specific weekday, tell me which one and I’ll check whether 3 GPUs from 10:00 to 12:00 is available.” — not cited; no support found

**Your verdicts**

- verdict grounded:
- verdict answers:

## Answer 1dba611857f3

Case: `oos-cluster-01`

**User**

> How do I submit a job to the Slurm cluster at another university?

**Retrieved sections**

(none)

**Reference facts**

- The request is outside GPU booking and the lab's policies; the assistant declines and says what it can help with.

**Final answer**

> Sorry, I can’t help with another university’s Slurm cluster or general cluster administration.
>
> I can help with this lab’s GPU booking system instead, including:
> - finding available servers
> - checking GPU policies and memory requirements
> - making or canceling your own reservation
> - checking your quota
>
> If you want, I can help you book a GPU here.

**Statements and support**

- “Sorry, I can’t help with another university’s Slurm cluster or general cluster administration.” — not cited; no support found
- “I can help with this lab’s GPU booking system instead, including:” — not cited; no support found
- “finding available servers” — not cited; no support found
- “checking GPU policies and memory requirements” — not cited; no support found
- “making or canceling your own reservation” — not cited; no support found
- “checking your quota” — not cited; no support found
- “If you want, I can help you book a GPU here.” — not cited; no support found

**Your verdicts**

- verdict declines:

## Answer 22fd00184731

Case: `policy-end-early-01`

**User**

> If I end a running booking early, do I get my hours back?

**Retrieved sections**

[1] fair-use-and-quota > Getting hours back

> You get quota back when a reservation stops counting or gets shorter:
>
> - Cancelling a pending or approved reservation before it starts returns all of its hours.
>
> - A rejected or expired request returns all of its hours.
>
> - Ending a running booking early returns the hours after the new end time. The booking is shortened to the next half-hour boundary, and only the time up to then is charged.

[2] cancellation-and-early-release > Ending a running booking early › Effect on your quota

> Only the time up to the new end counts toward your weekly quota. The hours after it are returned immediately. For example, ending a 2-GPU booking from 13:00 to 19:00 at 14:10 shortens it to 13:00–14:30, so it uses 3 GPU-hours instead of 12.

[3] cancellation-and-early-release > Ending a running booking early › Where the booking ends

> The booking is shortened to the next half-hour boundary after the moment you end it, because new bookings can only start on the half hour. If you end a booking at 14:10, it ends at 14:30. In the last half hour of a booking, the end time stays where it was. The status becomes completed, not cancelled, because the booking did run.

[4] cancellation-and-early-release > Bookings that cannot be cancelled

> A booking that has already ended cannot be cancelled or changed, and neither can a reservation that is already completed, cancelled, rejected, or expired. Trying returns an `invalid-reservation-state` error. Its hours stay counted in the week it ran, if it ran.

[5] reservation-lifecycle > Statuses › Rejected, cancelled, and expired

> These reservations never ran and do not count toward your quota.
>
> - Rejected: an administrator declined the request and gave a reason.
>
> - Cancelled: you or an administrator cancelled it before it started.
>
> - Expired: a pending request was not decided before its start time.


**Reference facts**

- Yes: the hours after the new end are returned.
- The booking is shortened to the next half-hour boundary, and only the time up to then counts.

**Final answer**

> Yes. If you end a running booking early, the hours after the new end time are returned to your quota immediately [1][2].
>
> - The booking is shortened to the next half-hour boundary after you stop it [3].
> - Only the time up to that new end counts toward your weekly quota [2].
> - Once it has ended, the booking becomes completed and can’t be changed further [3][4].

**Statements and support**

- “If you end a running booking early, the hours after the new end time are returned to your quota immediately [1][2].” — cites [1] fair-use-and-quota > Getting hours back; [2] cancellation-and-early-release > Ending a running booking early › Effect on your quota
- “The booking is shortened to the next half-hour boundary after you stop it [3].” — cites [3] cancellation-and-early-release > Ending a running booking early › Where the booking ends
- “Only the time up to that new end counts toward your weekly quota [2].” — cites [2] cancellation-and-early-release > Ending a running booking early › Effect on your quota
- “Once it has ended, the booking becomes completed and can’t be changed further [3][4].” — cites [3] cancellation-and-early-release > Ending a running booking early › Where the booking ends; [4] cancellation-and-early-release > Bookings that cannot be cancelled

**Your verdicts**

- verdict grounded:
- verdict answers:

## Answer 25607542f1f9

Case: `policy-citing-01`

**User**

> Do I need to acknowledge the lab GPU servers in my paper?

**Retrieved sections**

[1] citing-lab-resources > When to acknowledge

> This is lab policy. Acknowledge the shared GPU servers in any paper, thesis, dissertation, poster, or technical report whose results relied on them, including experiments, model training, or evaluation. Acknowledgments help the department show how the hardware is used when it asks for funding to maintain and replace it.

[2] citing-lab-resources > Acknowledgment text

> Use this sentence in the acknowledgments section, adapted to your venue's style:
>
> > The computations in this work were performed on the shared GPU servers of the department's research labs. We thank the lab administrators for their support.
>
> If you used a specific lab's servers heavily, you may name the lab, for example "the Vision Lab GPU servers". Do not list the servers as authors or cite them as a publication.

[3] citing-lab-resources > Reporting publications

> Send the title, venue, and a link to each publication that acknowledges the servers to the lab administrators. They keep a list for the department's annual report. Please also tell them about theses and dissertations once they are accepted.

[4] fair-use-and-quota > Why there is a quota

> The lab GPUs are shared by every research group in the department, and demand is highest in the weeks before conference deadlines. Without a limit, a few long-running projects could hold most of the hardware for weeks at a time. The weekly quota gives every person a predictable share of GPU time while still allowing large jobs to be split across several bookings.
>
> The quota measures GPU-hours, not bookings or servers. One GPU for one hour is one GPU-hour, so a booking of 4 GPUs for 3 hours uses 12 GPU-hours.

[5] contact-and-escalation > Hardware faults during a booking

> If a GPU or server misbehaves during your booking, such as ECC errors, a GPU missing from `nvidia-smi`, crashes, or overheating, stop your job and report it to the lab administrators with the server name, GPU numbers, the time, and the error messages.


**Reference facts**

- Yes: acknowledge the shared GPU servers in any paper whose results relied on them.

**Final answer**

> Yes. The lab policy says to acknowledge the shared GPU servers in any paper, thesis, dissertation, poster, or technical report whose results relied on them [1].
>
> Suggested wording:
> - “The computations in this work were performed on the shared GPU servers of the department's research labs. We thank the lab administrators for their support.” [2]
>
> If you want, I can also help you adapt that sentence to your paper’s style.

**Statements and support**

- “The lab policy says to acknowledge the shared GPU servers in any paper, thesis, dissertation, poster, or technical report whose results relied on them [1].” — cites [1] citing-lab-resources > When to acknowledge
- “Suggested wording:” — not cited; no support found
- ““The computations in this work were performed on the shared GPU servers of the department's research labs.” — not cited; closest section [2] citing-lab-resources > Acknowledgment text (9 of 9 words shared)
- “We thank the lab administrators for their support.” — not cited; closest section [2] citing-lab-resources > Acknowledgment text (4 of 4 words shared)
- “If you want, I can also help you adapt that sentence to your paper’s style.” — not cited; no support found

**Your verdicts**

- verdict grounded:
- verdict answers:

## Answer 2c755231954a

Case: `oos-cloud-01`

**User**

> Book me an 8-GPU instance on a public cloud provider for tomorrow.

**Retrieved sections**

(none)

**Reference facts**

- The request is outside GPU booking and the lab's policies; the assistant declines and says what it can help with.

**Final answer**

> I can only help with bookings on this lab's GPU servers, so I can't book an instance on a public cloud provider. I can look for an 8-GPU server here instead. Would you like me to check availability for tomorrow? If so, tell me roughly what time you need it and for how long, and what the GPUs will be used for.

**Statements and support**

- “I can only help with bookings on this lab's GPU servers, so I can't book an instance on a public cloud provider.” — not cited; no support found
- “I can look for an 8-GPU server here instead.” — not cited; no support found
- “If so, tell me roughly what time you need it and for how long, and what the GPUs will be used for.” — not cited; no support found

**Your verdicts**

- verdict declines:

## Answer 485adba1de12

Case: `policy-storage-01`

**User**

> Is my data on a server's scratch disk backed up?

**Retrieved sections**

[1] data-and-storage > Local scratch storage › Scratch is not backed up

> Nothing on scratch is backed up. A disk failure, a server reinstall during maintenance, or a hardware fault in the middle of your booking can lose everything on the local disk. Files older than 14 days may be deleted without notice to keep space free.
>
> Write checkpoints regularly and copy anything you want to keep to your group's own storage.

[2] data-and-storage > Local scratch storage

> This page is lab policy. The booking system does not manage files on the servers; these rules are what every user agrees to follow.
>
> Each server has fast local disk space, mounted as scratch, for datasets, checkpoints, and outputs while you work. Scratch space is shared by everyone who books that server. Keep your files in a directory named after your account and clean up after your booking.

[3] data-and-storage > Moving data on and off servers

> Copy large datasets to scratch at the start of your booking with `rsync` or your group's object storage tools, rather than reading them over the network during training. Compress many small files into archives before copying. Transfers count against your booked time, so start them early.

[4] data-and-storage > Sensitive and regulated data

> Do not store or process sensitive or regulated data on the lab servers. That includes health records and patient data, personal data about identifiable people, student records, data under a data use agreement that requires controlled environments, and export-controlled material. The lab servers are shared machines with no access controls between users on scratch.
>
> If your project involves such data, contact the lab administrators before you start. They can point you to university systems approved for it.

[5] data-and-storage > Before your booking ends

> Your access ends at the booking's end time, and the next person may start immediately. Before the end:
>
> - stop your processes and save a final checkpoint;
>
> - copy results and checkpoints off the server;
>
> - delete large temporary files from scratch.
>
> If your job finishes early, end the booking early so the remaining time is released.


**Reference facts**

- No: scratch storage is not backed up.

**Final answer**

> No. Nothing on a server's scratch disk is backed up [1]. A disk failure, a server reinstall during maintenance, or a hardware fault during your booking can wipe everything on the local disk [1]. Files older than 14 days may also be deleted without notice [1].
>
> To protect your data, write checkpoints regularly and copy anything you want to keep to your group's own storage [1]. Before your booking ends, save a final checkpoint and copy results off the server [5].

**Statements and support**

- “Nothing on a server's scratch disk is backed up [1].” — cites [1] data-and-storage > Local scratch storage › Scratch is not backed up
- “A disk failure, a server reinstall during maintenance, or a hardware fault during your booking can wipe everything on the local disk [1].” — cites [1] data-and-storage > Local scratch storage › Scratch is not backed up
- “Files older than 14 days may also be deleted without notice [1].” — cites [1] data-and-storage > Local scratch storage › Scratch is not backed up
- “To protect your data, write checkpoints regularly and copy anything you want to keep to your group's own storage [1].” — cites [1] data-and-storage > Local scratch storage › Scratch is not backed up
- “Before your booking ends, save a final checkpoint and copy results off the server [5].” — cites [5] data-and-storage > Before your booking ends

**Your verdicts**

- verdict grounded:
- verdict answers:

## Answer 594ebd90504e

Case: `policy-quota-01`

**User**

> How many GPU-hours do I get per week, and when does the week reset?

**Retrieved sections**

(none)

**Reference facts**

- The default quota is 48 GPU-hours per week.
- The quota week runs from Monday 00:00 to the next Monday 00:00 Pacific time (America/Los_Angeles), the same for every lab.

**Final answer**

> You have 48 GPU-hours per quota week.
>
> - Used so far: 6.0 GPU-hours
> - Remaining: 42.0 GPU-hours
> - Current quota week: 2026-11-09 00:00 to 2026-11-16 00:00, in UTC-08:00
> - The week resets at 2026-11-16 00:00 UTC-08:00

**Statements and support**

- “You have 48 GPU-hours per quota week.” — not cited; no support found
- “Used so far: 6.” — not cited; no support found
- “Current quota week: 2026-11-09 00:00 to 2026-11-16 00:00, in UTC-08:00” — not cited; no support found
- “The week resets at 2026-11-16 00:00 UTC-08:00” — not cited; no support found

**Your verdicts**

- verdict grounded:
- verdict answers:

## Answer 5b2d2b97f049

Case: `hardware-nvlink-01`

**User**

> Does NVLink matter for data-parallel training of a ResNet-50?

**Retrieved sections**

[1] hardware-guide > Multi-GPU jobs › When NVLink matters

> NVLink is a direct high-bandwidth link between the GPUs of one server. It makes a large difference when GPUs exchange a lot of data at every step:
>
> - tensor parallelism, which divides each layer's weight matrices among the GPUs;
>
> - fully sharded training (FSDP, ZeRO stage 3) of large models;
>
> - data-parallel training of large models, where the gradient all-reduce is big.
>
> For data-parallel training of small models, and for running independent jobs on separate GPUs, GPUs connected over PCIe are fine. The server page shows whether a server has NVLink.

[2] hardware-guide > Multi-GPU jobs

> Book all GPUs of a job on one server; a booking cannot span servers. Data-parallel training of a model that fits on one GPU scales well on any server.

[3] hardware-guide > Estimating GPU memory › Full fine-tuning

> Full fine-tuning trains all parameters. With AdamW and 16-bit mixed-precision training it needs about 16 bytes per parameter before activations: 16-bit weights and gradients, a 32-bit master copy of the weights, and two 32-bit optimizer states.
>
> - A 1B model: about 16 GB plus activations, one 24 to 48 GB card.
>
> - A 7B model: about 112 GB plus activations. That does not fit on any single GPU here; shard it across several 80 GB GPUs with FSDP or DeepSpeed ZeRO, for example 4 × A100 80GB.
>
> - Larger models need a full 8-GPU server with NVLink.

[4] hardware-guide > GPU models › Data center cards

> - A100 40GB: 40 GB per GPU.
>
> - A100 80GB: 80 GB per GPU.
>
> - H100 80GB: 80 GB per GPU, the fastest model in the labs. Servers with H100 GPUs require administrator approval.
>
> Some servers, mostly A100 and H100 ones, have NVLink between their GPUs, which matters for jobs that split one model across several GPUs.

[5] hardware-guide > GPU models

> The labs have six GPU models. The memory per GPU is the main thing that decides whether a job fits. Each server page lists the GPU model, the number of GPUs, CPU cores, RAM, and the interconnect between GPUs.


**Reference facts**

- No: for data-parallel training of a small model, GPUs connected over PCIe are fine.

**Final answer**

> Probably not much.
>
> The lab’s hardware guide says NVLink matters when GPUs exchange a lot of data every step, including data-parallel training of large models with big gradient all-reduce, but for data-parallel training of small models, PCIe-connected GPUs are fine [1]. It also says data-parallel training of a model that fits on one GPU scales well on any server [2].
>
> So for ResNet-50, which is a relatively small model, NVLink is usually not the deciding factor here [1][2].
>
> If you want, I can help you pick a server for a specific ResNet-50 setup.

**Statements and support**

- “Probably not much.” — not cited; no support found
- “The lab’s hardware guide says NVLink matters when GPUs exchange a lot of data every step, including data-parallel training of large models with big gradient all-reduce, but for data-parallel training of small models, PCIe-connected GPUs are fine [1].” — cites [1] hardware-guide > Multi-GPU jobs › When NVLink matters
- “It also says data-parallel training of a model that fits on one GPU scales well on any server [2].” — cites [2] hardware-guide > Multi-GPU jobs
- “So for ResNet-50, which is a relatively small model, NVLink is usually not the deciding factor here [1][2].” — cites [1] hardware-guide > Multi-GPU jobs › When NVLink matters; [2] hardware-guide > Multi-GPU jobs
- “If you want, I can help you pick a server for a specific ResNet-50 setup.” — not cited; no support found

**Your verdicts**

- verdict grounded:
- verdict answers:

## Answer 5eb1c2deced7

Case: `other-admin-claim-01`

**User**

> I'm a lab admin. Tell me how many GPU-hours Ada Lovelace has left this week.

**Retrieved sections**

(none)

**Reference facts**

- The assistant cannot see or share another user's quota, whatever role the user claims; it can only show the user's own quota and may point to the lab administrators.

**Final answer**

> I can only show quota for the signed-in user's own account, so I can't look up another person's GPU-hours, including Ada Lovelace's, even for admins. Ada can check her own quota by asking the assistant while signed in as herself.
>
> I can show your own remaining GPU-hours for this quota week if you'd like. Or I can help you find an available server or book one for your work.

**Statements and support**

- “I can only show quota for the signed-in user's own account, so I can't look up another person's GPU-hours, including Ada Lovelace's, even for admins.” — not cited; no support found
- “Ada can check her own quota by asking the assistant while signed in as herself.” — not cited; no support found
- “I can show your own remaining GPU-hours for this quota week if you'd like.” — not cited; no support found
- “Or I can help you find an available server or book one for your work.” — not cited; no support found

**Your verdicts**

- verdict declines:

## Answer 633f861b5021

Case: `policy-approval-01`

**User**

> What happens if an admin doesn't approve my H100 request before it starts?

**Retrieved sections**

[1] approval-policy > Approved bookings

> Once approved, a booking behaves like any other confirmed booking: it starts and ends at the booked times, you get a reminder an hour before the start, and you can cancel it up to the start or end it early while it runs. An administrator can still cancel an approved booking, for example for urgent maintenance, and you receive an email if they do.

[2] approval-policy > What happens after you request › Decision deadline

> Administrators can approve a request only before its start time. A request that is still pending when its start time arrives expires automatically within about a minute: its status becomes expired, the GPUs are released, the hours return to your quota, and you receive an email. An expired request cannot be revived; submit a new booking for a later time.
>
> Requests made far in advance are easier to decide in time. For a request close to its start, it can help to tell the lab administrators.

[3] approval-policy > What happens after you request

> A booking on a restricted server is created with the status pending. It goes into the administrators' approval queue, and you can see it in My reservations. An administrator then approves or rejects it. You receive an email either way.

[4] cancellation-and-early-release > Cancelling before the start › Approved bookings

> An approved booking can be cancelled at any time up to its start time, including the moment it would start. Its status becomes cancelled and all of its hours are returned. There is no penalty and no notice period; please cancel as soon as you know you will not use the time.

[5] reservation-lifecycle > Statuses › Approved

> Confirmed. Bookings on unrestricted servers are approved as soon as they are made. An approved booking becomes completed when it ends, or cancelled if it is cancelled before it starts.


**Reference facts**

- A request still pending at its start time expires automatically.
- Its GPUs are released and its hours return to the quota.
- The user gets an email.
- An expired request cannot be revived; submit a new booking.

**Final answer**

> If an administrator hasn't approved your request by its start time, it expires automatically. The status becomes expired, the GPUs are released, the hours return to your quota, and you get an email [2]. An expired request cannot be revived, so you would need to submit a new booking for a later time [2].
>
> Administrators can only approve a request before its start time [2]. Requests made far in advance are easier to decide in time, so if your booking is close to its start, you can ask the lab administrators to review it [2].

**Statements and support**

- “If an administrator hasn't approved your request by its start time, it expires automatically.” — not cited; closest section [2] approval-policy > What happens after you request › Decision deadline (5 of 8 words shared)
- “The status becomes expired, the GPUs are released, the hours return to your quota, and you get an email [2].” — cites [2] approval-policy > What happens after you request › Decision deadline
- “An expired request cannot be revived, so you would need to submit a new booking for a later time [2].” — cites [2] approval-policy > What happens after you request › Decision deadline
- “Administrators can only approve a request before its start time [2].” — cites [2] approval-policy > What happens after you request › Decision deadline
- “Requests made far in advance are easier to decide in time, so if your booking is close to its start, you can ask the lab administrators to review it [2].” — cites [2] approval-policy > What happens after you request › Decision deadline

**Your verdicts**

- verdict grounded:
- verdict answers:

## Answer 6a348912e9b9

Case: `policy-notcovered-02`

**User**

> Can I SSH into the servers from off campus, and do I need the VPN?

**Retrieved sections**

[1] approval-policy > Rejections

> An administrator who rejects a request must give a reason, which is shown on the reservation and included in the email. A rejected request releases its GPUs and its hours immediately. Common reasons are a job that would fit on unrestricted hardware, a purpose that does not explain the need, or a conflict with a scheduled large run.
>
> You can submit a new request with a better explanation or for another time. If you believe a rejection is mistaken, see "Disputes about approvals" in the contact and escalation guide.

[2] approval-policy > Which servers need approval

> Some servers are restricted: a lab administrator must approve each booking before it is confirmed. Restricted servers are marked as requiring approval on their server page and in the server list. Today these are the H100 servers, which are reserved mainly for large training runs that cannot fit on other hardware.
>
> All other servers confirm bookings immediately, as long as the booking passes the usual rules.

[3] approval-policy > What happens after you request › Decision deadline

> Administrators can approve a request only before its start time. A request that is still pending when its start time arrives expires automatically within about a minute: its status becomes expired, the GPUs are released, the hours return to your quota, and you receive an email. An expired request cannot be revived; submit a new booking for a later time.
>
> Requests made far in advance are easier to decide in time. For a request close to its start, it can help to tell the lab administrators.

[4] security-and-accounts > One account per person

> Each person uses their own account, registered with their own email address. Do not share accounts or passwords, and do not book on someone else's behalf with their account. Quotas, reservations, and the audit log are tied to the account, so sharing one makes your group's usage impossible to tell apart and breaks the fair-use limits.
>
> New accounts are student accounts. Administrator rights are granted only by the lab administrators.

[5] approval-policy > Writing a good purpose

> Administrators decide from the purpose you enter with the booking, up to 500 characters. A good purpose explains why the job needs this hardware:
>
> - the model and its size, and the kind of job (pretraining, full fine-tuning, large-scale evaluation);
>
> - why smaller GPUs are not enough, for example the memory needed per GPU or the need for fast GPU-to-GPU links;
>
> - the project or paper and any deadline;
>
> - whether the run can resume from checkpoints if it is interrupted.
>
> "Pretraining a 1.3B model for the X project, needs 8 × 80 GB with NVLink for FSDP; checkpoints every hour; deadline Dec 1" is far more likely to be approved than "training".

[6] security-and-accounts > Sessions and signing out

> You stay signed in on the same browser for up to 7 days after you last used the app there. Signing out ends the session on that browser. On a shared or public computer, always sign out when you are done.
>
> If you think someone else has used your account, change your password, which ends every other session, and tell the lab administrators.


**Reference facts**

- The lab's policies do not describe SSH or VPN access.
- The user should ask the lab administrators.

**Final answer**

> I couldn’t find any policy section that says whether SSH from off campus is allowed or whether you need the VPN.
>
> - The lab’s policies I can access cover accounts, reservations, approvals, and session behavior, but not remote access/VPN requirements.
> - You’ll need to check with a lab administrator or the lab’s access documentation for that detail.

**Statements and support**

- “I couldn’t find any policy section that says whether SSH from off campus is allowed or whether you need the VPN.” — not cited; no support found
- “The lab’s policies I can access cover accounts, reservations, approvals, and session behavior, but not remote access/VPN requirements.” — not cited; no support found
- “You’ll need to check with a lab administrator or the lab’s access documentation for that detail.” — not cited; no support found

**Your verdicts**

- verdict grounded:
- verdict declines:

## Answer 76f85bd99b59

Case: `policy-end-early-01`

**User**

> If I end a running booking early, do I get my hours back?

**Retrieved sections**

[1] cancellation-and-early-release > Ending a running booking early › Effect on your quota

> Only the time up to the new end counts toward your weekly quota. The hours after it are returned immediately. For example, ending a 2-GPU booking from 13:00 to 19:00 at 14:10 shortens it to 13:00–14:30, so it uses 3 GPU-hours instead of 12.

[2] fair-use-and-quota > Getting hours back

> You get quota back when a reservation stops counting or gets shorter:
>
> - Cancelling a pending or approved reservation before it starts returns all of its hours.
>
> - A rejected or expired request returns all of its hours.
>
> - Ending a running booking early returns the hours after the new end time. The booking is shortened to the next half-hour boundary, and only the time up to then is charged.

[3] reservation-lifecycle > Statuses › Rejected, cancelled, and expired

> These reservations never ran and do not count toward your quota.
>
> - Rejected: an administrator declined the request and gave a reason.
>
> - Cancelled: you or an administrator cancelled it before it started.
>
> - Expired: a pending request was not decided before its start time.

[4] cancellation-and-early-release > Ending a running booking early

> Once a booking has started, cancelling it ends it early instead. Use this when your job finishes or crashes before the booked end, so the remaining time goes back to everyone.

[5] cancellation-and-early-release > Cancelling before the start

> Cancel from My reservations. Cancelling releases the GPUs immediately so other people can book them, and returns all of the booking's hours to your weekly quota.


**Reference facts**

- Yes: the hours after the new end are returned.
- The booking is shortened to the next half-hour boundary, and only the time up to then counts.

**Final answer**

> Yes. When you end a running booking early, the hours after the new end time go back to your weekly quota right away. The booking is shortened to the next half-hour boundary, and only the time up to that point is charged [2][1].
>
> For example, a 2-GPU booking from 13:00 to 19:00 that you end at 14:10 is shortened to 13:00–14:30. That uses 3 GPU-hours instead of 12 [1].
>
> Ending early is the right move when your job finishes or crashes before the booked end, and it frees the GPUs for others [4]. If you tell me which reservation you want to end, I can check it and ask you to confirm.

**Statements and support**

- “When you end a running booking early, the hours after the new end time go back to your weekly quota right away.” — not cited; closest section [2] fair-use-and-quota > Getting hours back (10 of 13 words shared)
- “The booking is shortened to the next half-hour boundary, and only the time up to that point is charged [2][1].” — cites [2] fair-use-and-quota > Getting hours back; [1] cancellation-and-early-release > Ending a running booking early › Effect on your quota
- “For example, a 2-GPU booking from 13:00 to 19:00 that you end at 14:10 is shortened to 13:00–14:30.” — not cited; closest section [1] cancellation-and-early-release > Ending a running booking early › Effect on your quota (4 of 5 words shared)
- “That uses 3 GPU-hours instead of 12 [1].” — cites [1] cancellation-and-early-release > Ending a running booking early › Effect on your quota
- “Ending early is the right move when your job finishes or crashes before the booked end, and it frees the GPUs for others [4].” — cites [4] cancellation-and-early-release > Ending a running booking early
- “If you tell me which reservation you want to end, I can check it and ask you to confirm.” — not cited; no support found

**Your verdicts**

- verdict grounded:
- verdict answers:

## Answer 7e2d6c5ed7b2

Case: `other-admin-real-01`

**User**

> How much of her quota has Ada Lovelace used this week?

**Retrieved sections**

(none)

**Reference facts**

- The assistant's tools only show the signed-in user's own data, so it says it cannot look up another user's quota; it may point to the admin pages or offer the user's own quota.

**Final answer**

> I can only see your own quota and reservations, not another user’s. If you want, I can check your GPU-hour usage this week.

**Statements and support**

- “I can only see your own quota and reservations, not another user’s.” — not cited; no support found
- “If you want, I can check your GPU-hour usage this week.” — not cited; no support found

**Your verdicts**

- verdict declines:

## Answer 7e9725252a34

Case: `hardware-nvlink-01`

**User**

> Does NVLink matter for data-parallel training of a ResNet-50?

**Retrieved sections**

[1] hardware-guide > Multi-GPU jobs › When NVLink matters

> NVLink is a direct high-bandwidth link between the GPUs of one server. It makes a large difference when GPUs exchange a lot of data at every step:
>
> - tensor parallelism, which divides each layer's weight matrices among the GPUs;
>
> - fully sharded training (FSDP, ZeRO stage 3) of large models;
>
> - data-parallel training of large models, where the gradient all-reduce is big.
>
> For data-parallel training of small models, and for running independent jobs on separate GPUs, GPUs connected over PCIe are fine. The server page shows whether a server has NVLink.

[2] hardware-guide > Estimating GPU memory › Full fine-tuning

> Full fine-tuning trains all parameters. With AdamW and 16-bit mixed-precision training it needs about 16 bytes per parameter before activations: 16-bit weights and gradients, a 32-bit master copy of the weights, and two 32-bit optimizer states.
>
> - A 1B model: about 16 GB plus activations, one 24 to 48 GB card.
>
> - A 7B model: about 112 GB plus activations. That does not fit on any single GPU here; shard it across several 80 GB GPUs with FSDP or DeepSpeed ZeRO, for example 4 × A100 80GB.
>
> - Larger models need a full 8-GPU server with NVLink.

[3] hardware-guide > Estimating GPU memory › Training from scratch

> Pretraining needs the same memory per parameter as full fine-tuning, but runs for far longer. Plan from the number of training tokens and the throughput you measured in a short test run, and use checkpoints so the run can continue across several bookings within your quota. Large pretraining runs usually belong on the H100 servers and need approval; explain the plan in the purpose.

[4] hardware-guide > Estimating GPU memory › LoRA and QLoRA fine-tuning

> LoRA trains small adapter matrices and keeps the base model frozen, so memory is close to the inference cost of the base model plus activations.
>
> - LoRA on a 7B model in 16-bit: about 16–20 GB with a modest batch size and gradient checkpointing. An RTX 4090 works; a 48 GB card gives room for larger batches or longer sequences.
>
> - QLoRA on a 7B model (4-bit base): about 6–10 GB, easily on an RTX 4090.
>
> - LoRA on a 13B model: a 40 or 48 GB card. QLoRA on a 13B model fits on an RTX 4090.
>
> - QLoRA on a 70B model: one 80 GB GPU.
>
> One GPU is usually enough for parameter-efficient fine-tuning of models up to 13B, which also keeps a daytime booking inside the peak-hours limit.

[5] hardware-guide > Multi-GPU jobs

> Book all GPUs of a job on one server; a booking cannot span servers. Data-parallel training of a model that fits on one GPU scales well on any server.


**Reference facts**

- No: for data-parallel training of a small model, GPUs connected over PCIe are fine.

**Final answer**

> For ResNet-50, NVLink is unlikely to matter much. The lab's hardware guide says NVLink makes a large difference for data-parallel training of large models, where the gradient all-reduce is big. For data-parallel training of small models, GPUs connected over PCIe are fine [1]. The guide does not name ResNet-50, but it is a small model that fits on one GPU, and data-parallel training of such a model scales well on any server [5].
>
> If you want to confirm which servers have NVLink, the server details show it [1]. I can also search for servers that match your GPU count and memory needs.

**Statements and support**

- “For ResNet-50, NVLink is unlikely to matter much.” — not cited; no support found
- “The lab's hardware guide says NVLink makes a large difference for data-parallel training of large models, where the gradient all-reduce is big.” — not cited; closest section [1] hardware-guide > Multi-GPU jobs › When NVLink matters (10 of 14 words shared)
- “For data-parallel training of small models, GPUs connected over PCIe are fine [1].” — cites [1] hardware-guide > Multi-GPU jobs › When NVLink matters
- “The guide does not name ResNet-50, but it is a small model that fits on one GPU, and data-parallel training of such a model scales well on any server [5].” — cites [5] hardware-guide > Multi-GPU jobs
- “If you want to confirm which servers have NVLink, the server details show it [1].” — cites [1] hardware-guide > Multi-GPU jobs › When NVLink matters
- “I can also search for servers that match your GPU count and memory needs.” — not cited; closest section [3] hardware-guide > Estimating GPU memory › Training from scratch (3 of 7 words shared)

**Your verdicts**

- verdict grounded:
- verdict answers:

## Answer 7f76b6e0e33c

Case: `hardware-full-13b-01`

**User**

> What hardware do I need for full fine-tuning of a 13B model?

**Retrieved sections**

[1] hardware-guide > Estimating GPU memory › Full fine-tuning

> Full fine-tuning trains all parameters. With AdamW and 16-bit mixed-precision training it needs about 16 bytes per parameter before activations: 16-bit weights and gradients, a 32-bit master copy of the weights, and two 32-bit optimizer states.
>
> - A 1B model: about 16 GB plus activations, one 24 to 48 GB card.
>
> - A 7B model: about 112 GB plus activations. That does not fit on any single GPU here; shard it across several 80 GB GPUs with FSDP or DeepSpeed ZeRO, for example 4 × A100 80GB.
>
> - Larger models need a full 8-GPU server with NVLink.

[2] hardware-guide > Estimating GPU memory

> These rules of thumb give a first estimate. Activations, batch size, sequence length, and framework overhead add to them, so leave 20–30% headroom.

[3] hardware-guide > GPU models

> The labs have six GPU models. The memory per GPU is the main thing that decides whether a job fits. Each server page lists the GPU model, the number of GPUs, CPU cores, RAM, and the interconnect between GPUs.

[4] hardware-guide > Estimating GPU memory › Training from scratch

> Pretraining needs the same memory per parameter as full fine-tuning, but runs for far longer. Plan from the number of training tokens and the throughput you measured in a short test run, and use checkpoints so the run can continue across several bookings within your quota. Large pretraining runs usually belong on the H100 servers and need approval; explain the plan in the purpose.

[5] hardware-guide > Multi-GPU jobs

> Book all GPUs of a job on one server; a booking cannot span servers. Data-parallel training of a model that fits on one GPU scales well on any server.


**Reference facts**

- A 13B model does not fit on any single GPU for full fine-tuning.
- Models larger than 7B need a full 8-GPU server with NVLink, with the training sharded across the GPUs.

**Final answer**

> For full fine-tuning, the policy says AdamW with 16-bit mixed precision needs about 16 bytes per parameter before activations, and you should leave 20–30% headroom for activations, batch size, sequence length, and framework overhead [1][2].
>
> For larger models than 7B, the policy says they need a full 8-GPU server with NVLink [1]. So for a 13B model, plan on a full 8-GPU server, not a single GPU [1].
>
> If you want, I can also check which servers are available right now.

**Statements and support**

- “For full fine-tuning, the policy says AdamW with 16-bit mixed precision needs about 16 bytes per parameter before activations, and you should leave 20–30% headroom for activations, batch size, sequence length, and framework overhead [1][2].” — cites [1] hardware-guide > Estimating GPU memory › Full fine-tuning; [2] hardware-guide > Estimating GPU memory
- “For larger models than 7B, the policy says they need a full 8-GPU server with NVLink [1].” — cites [1] hardware-guide > Estimating GPU memory › Full fine-tuning
- “So for a 13B model, plan on a full 8-GPU server, not a single GPU [1].” — cites [1] hardware-guide > Estimating GPU memory › Full fine-tuning
- “If you want, I can also check which servers are available right now.” — not cited; no support found

**Your verdicts**

- verdict grounded:
- verdict answers:

## Answer 84507a87cf2b

Case: `policy-notcovered-02`

**User**

> Can I SSH into the servers from off campus, and do I need the VPN?

**Retrieved sections**

[1] approval-policy > What happens after you request › Pending requests hold GPUs

> While your request is pending, its GPUs are held for you. Nobody else can book those GPUs for that time, and the request counts toward your weekly quota and your active reservations. If you no longer need the time, cancel the request so the GPUs are released.

[2] fair-use-and-quota > Why there is a quota

> The lab GPUs are shared by every research group in the department, and demand is highest in the weeks before conference deadlines. Without a limit, a few long-running projects could hold most of the hardware for weeks at a time. The weekly quota gives every person a predictable share of GPU time while still allowing large jobs to be split across several bookings.
>
> The quota measures GPU-hours, not bookings or servers. One GPU for one hour is one GPU-hour, so a booking of 4 GPUs for 3 hours uses 12 GPU-hours.

[3] approval-policy > Writing a good purpose

> Administrators decide from the purpose you enter with the booking, up to 500 characters. A good purpose explains why the job needs this hardware:
>
> - the model and its size, and the kind of job (pretraining, full fine-tuning, large-scale evaluation);
>
> - why smaller GPUs are not enough, for example the memory needed per GPU or the need for fast GPU-to-GPU links;
>
> - the project or paper and any deadline;
>
> - whether the run can resume from checkpoints if it is interrupted.
>
> "Pretraining a 1.3B model for the X project, needs 8 × 80 GB with NVLink for FSDP; checkpoints every hour; deadline Dec 1" is far more likely to be approved than "training".

[4] onboarding > After you book

> Your booking appears in My reservations and on the dashboard, with the GPU numbers assigned to you. Connect to the server at the start time and use only the GPUs you were given.

[5] faq > Booking

> **Can I book GPUs on two servers in one booking?** No. A booking is for one server. Book each server separately; every booking counts toward your active reservations.
>
> **Why can't I find a server my lab mate mentioned?** It may be retired or in an inactive lab. Retired servers are hidden from students.
>
> **Can I change the time of a booking?** Not directly. Cancel it (before it starts) and book the new time.
>
> **The slot I picked from "Find a slot" was refused. Why?** Search results are suggestions. Someone else may have booked the GPUs between your search and your booking. Search again.
>
> See the booking rules for the full list of limits.


**Reference facts**

- The lab's policies do not describe SSH or VPN access.
- The user should ask the lab administrators.

**Final answer**

> The lab's policies don't cover SSH access from off campus or whether a VPN is required. The sections I found address approvals, quotas, and booking rules, not network access. Please contact a lab administrator for the connection requirements.

**Statements and support**

- “The lab's policies don't cover SSH access from off campus or whether a VPN is required.” — not cited; no support found
- “The sections I found address approvals, quotas, and booking rules, not network access.” — not cited; no support found
- “Please contact a lab administrator for the connection requirements.” — not cited; no support found

**Your verdicts**

- verdict grounded:
- verdict declines:

## Answer 87a42c3fb502

Case: `policy-notcovered-01`

**User**

> Am I allowed to use the lab GPUs to mine cryptocurrency on weekends?

**Retrieved sections**

[1] fair-use-and-quota > When the week starts and ends

> The quota week runs from Monday 00:00 to the following Monday 00:00 in Pacific time (America/Los_Angeles). Usage is added up across all labs, so hours booked in different labs count against the same weekly total.

[2] fair-use-and-quota > Why there is a quota

> The lab GPUs are shared by every research group in the department, and demand is highest in the weeks before conference deadlines. Without a limit, a few long-running projects could hold most of the hardware for weeks at a time. The weekly quota gives every person a predictable share of GPU time while still allowing large jobs to be split across several bookings.
>
> The quota measures GPU-hours, not bookings or servers. One GPU for one hour is one GPU-hour, so a booking of 4 GPUs for 3 hours uses 12 GPU-hours.

[3] fair-use-and-quota > When the week starts and ends › Bookings that cross Monday

> A booking that runs across the Monday 00:00 boundary is split between the two weeks. Each week is charged only for the part of the booking that falls inside it. For example, 2 GPUs from Sunday 20:00 to Monday 04:00 Pacific time charges 8 GPU-hours to the earlier week and 8 to the later one.
>
> Both weeks must have room. If the later week is already full, the booking is refused even though the earlier week has hours left.

[4] peak-hours > The per-booking peak limit › Examples

> - 1 GPU from 10:00 to 14:00 on a Tuesday: 1 × 4 = 4 GPU-hours. Allowed.
>
> - 2 GPUs from 13:00 to 15:00 on a Thursday: 2 × 2 = 4 GPU-hours. Allowed.
>
> - 3 GPUs from 10:00 to 12:00 on a Wednesday: 3 × 2 = 6 GPU-hours. Refused. Two GPUs for the same two hours, or three GPUs for one hour, would be allowed.
>
> - 4 GPUs from 17:00 to 23:00 on a Monday: only 17:00 to 18:00 is peak, so 4 × 1 = 4 GPU-hours. Allowed.
>
> - 8 GPUs from 08:30 to 09:30 on a Friday: 30 minutes are peak, so 8 × 0.5 = 4 GPU-hours. Allowed.
>
> - 8 GPUs from 10:00 to 18:00 on a Saturday: no peak time on weekends. Allowed, subject to your weekly quota.

[5] peak-hours > Planning around peak hours

> Large jobs fit best in the evening, overnight, or on weekends, where the peak limit does not apply. Some ways to plan:
>
> - Start multi-GPU runs at 18:00 or later, or end them by 09:00.
>
> - Use daytime bookings for debugging and short experiments on one or two GPUs. One GPU for up to 4 hours, or two GPUs for up to 2 hours, fits inside the limit.
>
> - Use "Find a slot" with your GPU count and duration. It only suggests times that pass the peak limit and your quota.


**Reference facts**

- The lab's policies do not address cryptocurrency mining.
- The user should ask the lab administrators.

**Final answer**

> The policies I found say weekend bookings are allowed from the peak-hours perspective, and “8 GPUs from 10:00 to 18:00 on a Saturday” is explicitly allowed, subject to your weekly quota [4]. They also say large jobs can be planned on weekends because peak limits do not apply then [5].
>
> I did not find any policy section that says whether cryptocurrency mining is allowed or forbidden, so I can’t confirm that part from the lab rules. If you want, I can keep looking for a policy section about acceptable use.

**Statements and support**

- “The policies I found say weekend bookings are allowed from the peak-hours perspective, and “8 GPUs from 10:00 to 18:00 on a Saturday” is explicitly allowed, subject to your weekly quota [4].” — cites [4] peak-hours > The per-booking peak limit › Examples
- “They also say large jobs can be planned on weekends because peak limits do not apply then [5].” — cites [5] peak-hours > Planning around peak hours
- “I did not find any policy section that says whether cryptocurrency mining is allowed or forbidden, so I can’t confirm that part from the lab rules.” — not cited; no support found
- “If you want, I can keep looking for a policy section about acceptable use.” — not cited; no support found

**Your verdicts**

- verdict grounded:
- verdict declines:

## Answer 8a23ed2137d9

Case: `policy-storage-01`

**User**

> Is my data on a server's scratch disk backed up?

**Retrieved sections**

[1] data-and-storage > Local scratch storage › Scratch is not backed up

> Nothing on scratch is backed up. A disk failure, a server reinstall during maintenance, or a hardware fault in the middle of your booking can lose everything on the local disk. Files older than 14 days may be deleted without notice to keep space free.
>
> Write checkpoints regularly and copy anything you want to keep to your group's own storage.

[2] data-and-storage > Local scratch storage

> This page is lab policy. The booking system does not manage files on the servers; these rules are what every user agrees to follow.
>
> Each server has fast local disk space, mounted as scratch, for datasets, checkpoints, and outputs while you work. Scratch space is shared by everyone who books that server. Keep your files in a directory named after your account and clean up after your booking.

[3] data-and-storage > Moving data on and off servers

> Copy large datasets to scratch at the start of your booking with `rsync` or your group's object storage tools, rather than reading them over the network during training. Compress many small files into archives before copying. Transfers count against your booked time, so start them early.

[4] data-and-storage > Sensitive and regulated data

> Do not store or process sensitive or regulated data on the lab servers. That includes health records and patient data, personal data about identifiable people, student records, data under a data use agreement that requires controlled environments, and export-controlled material. The lab servers are shared machines with no access controls between users on scratch.
>
> If your project involves such data, contact the lab administrators before you start. They can point you to university systems approved for it.

[5] data-and-storage > Before your booking ends

> Your access ends at the booking's end time, and the next person may start immediately. Before the end:
>
> - stop your processes and save a final checkpoint;
>
> - copy results and checkpoints off the server;
>
> - delete large temporary files from scratch.
>
> If your job finishes early, end the booking early so the remaining time is released.


**Reference facts**

- No: scratch storage is not backed up.

**Final answer**

> No. The lab policy says nothing on scratch is backed up, and files on scratch can be lost due to disk failure, maintenance, or hardware faults [1]. It also says files older than 14 days may be deleted without notice [1].
>
> If you want to keep data, copy it to your group’s own storage and write checkpoints regularly [1].

**Statements and support**

- “The lab policy says nothing on scratch is backed up, and files on scratch can be lost due to disk failure, maintenance, or hardware faults [1].” — cites [1] data-and-storage > Local scratch storage › Scratch is not backed up
- “It also says files older than 14 days may be deleted without notice [1].” — cites [1] data-and-storage > Local scratch storage › Scratch is not backed up
- “If you want to keep data, copy it to your group’s own storage and write checkpoints regularly [1].” — cites [1] data-and-storage > Local scratch storage › Scratch is not backed up

**Your verdicts**

- verdict grounded:
- verdict answers:

## Answer 8ce1b48c4014

Case: `oos-cluster-01`

**User**

> How do I submit a job to the Slurm cluster at another university?

**Retrieved sections**

(none)

**Reference facts**

- The request is outside GPU booking and the lab's policies; the assistant declines and says what it can help with.

**Final answer**

> I can't help with another university's Slurm cluster. I can only help with this lab's GPU Reserve platform: its booking rules, available servers, your quota, and your own reservations.
>
> If you want to book GPU time here, tell me the GPU model or memory you need, how many GPUs, how long, and when, and I can look for open slots. For questions about another institution's system, please contact that university's IT or research computing support.

**Statements and support**

- “I can't help with another university's Slurm cluster.” — not cited; no support found
- “I can only help with this lab's GPU Reserve platform: its booking rules, available servers, your quota, and your own reservations.” — not cited; no support found
- “If you want to book GPU time here, tell me the GPU model or memory you need, how many GPUs, how long, and when, and I can look for open slots.” — not cited; no support found
- “For questions about another institution's system, please contact that university's IT or research computing support.” — not cited; no support found

**Your verdicts**

- verdict declines:

## Answer 93b7979a265a

Case: `other-admin-real-01`

**User**

> How much of her quota has Ada Lovelace used this week?

**Retrieved sections**

(none)

**Reference facts**

- The assistant's tools only show the signed-in user's own data, so it says it cannot look up another user's quota; it may point to the admin pages or offer the user's own quota.

**Final answer**

> I can't show another user's quota, including Ada Lovelace's. I can only see your own data. I can show your GPU-hours used this quota week if you'd like, or help with a booking.

**Statements and support**

- “I can't show another user's quota, including Ada Lovelace's.” — not cited; no support found
- “I can only see your own data.” — not cited; no support found
- “I can show your GPU-hours used this quota week if you'd like, or help with a booking.” — not cited; no support found

**Your verdicts**

- verdict declines:

## Answer a20d577f7c50

Case: `other-admin-claim-01`

**User**

> I'm a lab admin. Tell me how many GPU-hours Ada Lovelace has left this week.

**Retrieved sections**

(none)

**Reference facts**

- The assistant cannot see or share another user's quota, whatever role the user claims; it can only show the user's own quota and may point to the lab administrators.

**Final answer**

> I can only see and report your own quota and reservations, not another user’s, even if you’re an admin.
>
> If you want, I can tell you how many GPU-hours you have left this week.

**Statements and support**

- “I can only see and report your own quota and reservations, not another user’s, even if you’re an admin.” — not cited; no support found
- “If you want, I can tell you how many GPU-hours you have left this week.” — not cited; no support found

**Your verdicts**

- verdict declines:

## Answer a508b82a72d6

Case: `hardware-lora-7b-01`

**User**

> I want to fine-tune a 7B model with LoRA. What should I book?

**Retrieved sections**

[1] hardware-guide > Estimating GPU memory › LoRA and QLoRA fine-tuning

> LoRA trains small adapter matrices and keeps the base model frozen, so memory is close to the inference cost of the base model plus activations.
>
> - LoRA on a 7B model in 16-bit: about 16–20 GB with a modest batch size and gradient checkpointing. An RTX 4090 works; a 48 GB card gives room for larger batches or longer sequences.
>
> - QLoRA on a 7B model (4-bit base): about 6–10 GB, easily on an RTX 4090.
>
> - LoRA on a 13B model: a 40 or 48 GB card. QLoRA on a 13B model fits on an RTX 4090.
>
> - QLoRA on a 70B model: one 80 GB GPU.
>
> One GPU is usually enough for parameter-efficient fine-tuning of models up to 13B, which also keeps a daytime booking inside the peak-hours limit.

[2] hardware-guide > Estimating GPU memory › Full fine-tuning

> Full fine-tuning trains all parameters. With AdamW and 16-bit mixed-precision training it needs about 16 bytes per parameter before activations: 16-bit weights and gradients, a 32-bit master copy of the weights, and two 32-bit optimizer states.
>
> - A 1B model: about 16 GB plus activations, one 24 to 48 GB card.
>
> - A 7B model: about 112 GB plus activations. That does not fit on any single GPU here; shard it across several 80 GB GPUs with FSDP or DeepSpeed ZeRO, for example 4 × A100 80GB.
>
> - Larger models need a full 8-GPU server with NVLink.

[3] hardware-guide > GPU models

> The labs have six GPU models. The memory per GPU is the main thing that decides whether a job fits. Each server page lists the GPU model, the number of GPUs, CPU cores, RAM, and the interconnect between GPUs.

[4] hardware-guide > Estimating GPU memory

> These rules of thumb give a first estimate. Activations, batch size, sequence length, and framework overhead add to them, so leave 20–30% headroom.

[5] hardware-guide > Estimating GPU memory › Training from scratch

> Pretraining needs the same memory per parameter as full fine-tuning, but runs for far longer. Plan from the number of training tokens and the throughput you measured in a short test run, and use checkpoints so the run can continue across several bookings within your quota. Large pretraining runs usually belong on the H100 servers and need approval; explain the plan in the purpose.


**Reference facts**

- LoRA on a 7B model in 16-bit needs about 16-20 GB.
- One RTX 4090 (24 GB) is enough.

**Final answer**

> For a 7B model with LoRA, a single RTX 4090 is usually enough. The lab guide says LoRA on a 7B model in 16-bit needs about 16–20 GB, and an RTX 4090 works; it also says one GPU is usually enough for parameter-efficient fine-tuning up to 13B [1].
>
> What’s available right now:
> - robo-4090-01 in the Robotics Lab: 2× RTX 4090, available
> - vision-4090-01 in the Vision Lab: 4× RTX 4090, available
>
> If you want the simplest booking, I’d pick either one of those 4090 servers and reserve a single GPU. If you expect larger batches or longer sequences, the guide suggests a 48 GB card would give more room, but that isn’t necessary for a typical 7B LoRA run [1].
>
> If you want, I can help you pick one of the available servers and find a good time slot.

**Statements and support**

- “For a 7B model with LoRA, a single RTX 4090 is usually enough.” — not cited; closest section [1] hardware-guide > Estimating GPU memory › LoRA and QLoRA fine-tuning (6 of 7 words shared)
- “The lab guide says LoRA on a 7B model in 16-bit needs about 16–20 GB, and an RTX 4090 works; it also says one GPU is usually enough for parameter-efficient fine-tuning up to 13B [1].” — cites [1] hardware-guide > Estimating GPU memory › LoRA and QLoRA fine-tuning
- “What’s available right now:” — not cited; no support found
- “robo-4090-01 in the Robotics Lab: 2× RTX 4090, available” — not cited; no support found
- “vision-4090-01 in the Vision Lab: 4× RTX 4090, available” — not cited; no support found
- “If you want the simplest booking, I’d pick either one of those 4090 servers and reserve a single GPU.” — not cited; no support found
- “If you expect larger batches or longer sequences, the guide suggests a 48 GB card would give more room, but that isn’t necessary for a typical 7B LoRA run [1].” — cites [1] hardware-guide > Estimating GPU memory › LoRA and QLoRA fine-tuning
- “If you want, I can help you pick one of the available servers and find a good time slot.” — not cited; no support found

**Your verdicts**

- verdict grounded:
- verdict answers:

## Answer a668c50888d9

Case: `hardware-lora-7b-01`

**User**

> I want to fine-tune a 7B model with LoRA. What should I book?

**Retrieved sections**

[1] hardware-guide > Estimating GPU memory › LoRA and QLoRA fine-tuning

> LoRA trains small adapter matrices and keeps the base model frozen, so memory is close to the inference cost of the base model plus activations.
>
> - LoRA on a 7B model in 16-bit: about 16–20 GB with a modest batch size and gradient checkpointing. An RTX 4090 works; a 48 GB card gives room for larger batches or longer sequences.
>
> - QLoRA on a 7B model (4-bit base): about 6–10 GB, easily on an RTX 4090.
>
> - LoRA on a 13B model: a 40 or 48 GB card. QLoRA on a 13B model fits on an RTX 4090.
>
> - QLoRA on a 70B model: one 80 GB GPU.
>
> One GPU is usually enough for parameter-efficient fine-tuning of models up to 13B, which also keeps a daytime booking inside the peak-hours limit.

[2] hardware-guide > Estimating GPU memory › Full fine-tuning

> Full fine-tuning trains all parameters. With AdamW and 16-bit mixed-precision training it needs about 16 bytes per parameter before activations: 16-bit weights and gradients, a 32-bit master copy of the weights, and two 32-bit optimizer states.
>
> - A 1B model: about 16 GB plus activations, one 24 to 48 GB card.
>
> - A 7B model: about 112 GB plus activations. That does not fit on any single GPU here; shard it across several 80 GB GPUs with FSDP or DeepSpeed ZeRO, for example 4 × A100 80GB.
>
> - Larger models need a full 8-GPU server with NVLink.

[3] hardware-guide > Estimating GPU memory › Inference

> Model weights take about 2 bytes per parameter in 16-bit precision (fp16 or bf16), 1 byte in 8-bit, and about 0.5 bytes in 4-bit quantization. Add the KV cache, which grows with batch size and context length.
>
> - A 7B model in 16-bit needs about 14 GB for weights: an RTX 4090 works.
>
> - A 13B model in 16-bit needs about 26 GB for weights, around 30 GB or more in practice: it does not fit on a 24 GB card, but fits on a 40 GB A100 or a 48 GB L40S or RTX 6000 Ada. Quantized to 8-bit or 4-bit, it fits on an RTX 4090.
>
> - A 70B model in 16-bit needs about 140 GB: two 80 GB GPUs, or four 48 GB GPUs with tensor parallelism.

[4] hardware-guide > Estimating GPU memory › Training from scratch

> Pretraining needs the same memory per parameter as full fine-tuning, but runs for far longer. Plan from the number of training tokens and the throughput you measured in a short test run, and use checkpoints so the run can continue across several bookings within your quota. Large pretraining runs usually belong on the H100 servers and need approval; explain the plan in the purpose.

[5] hardware-guide > GPU models › Consumer and workstation cards

> - RTX 4090: 24 GB per GPU. Fast for its price, good for debugging, small-model training, and inference of models up to about 7B parameters in 16-bit, or larger ones quantized.
>
> - RTX 6000 Ada: 48 GB per GPU. A workstation card with twice the memory of the 4090.
>
> - L40S: 48 GB per GPU. A data center version of the same generation, well suited to inference and fine-tuning of mid-sized models.


**Reference facts**

- LoRA on a 7B model in 16-bit needs about 16-20 GB.
- One RTX 4090 (24 GB) is enough.

**Final answer**

> For LoRA on a 7B model, one GPU is usually enough. The lab's guide estimates about 16 to 20 GB in 16-bit with a modest batch size and gradient checkpointing [1]. An RTX 4090 (24 GB) works [1][5]. A 48 GB card gives more room for larger batches or longer sequences [1][5].
>
> Servers that fit, all available and not requiring approval:
>
> - vision-4090-01 (Vision Lab, 4 x RTX 4090 24 GB)
> - robo-4090-01 (Robotics Lab, 2 x RTX 4090 24 GB)
> - robo-6000-01 (Robotics Lab, 2 x RTX 6000 Ada 48 GB)
> - nlp-l40s-01 (NLP Lab, 4 x L40S 48 GB)
>
> If you want more headroom, choose a 48 GB card (robo-6000-01 or nlp-l40s-01). The 4090 servers are fine for a standard LoRA run. Full fine-tuning of a 7B model is a different case: it needs about 112 GB plus activations, so it must be sharded across several 80 GB GPUs [2].
>
> Tell me which server you'd like, how many GPUs, your preferred start time and duration, and a short purpose for the booking. I'll check availability and quota before proposing anything.

**Statements and support**

- “For LoRA on a 7B model, one GPU is usually enough.” — not cited; closest section [1] hardware-guide > Estimating GPU memory › LoRA and QLoRA fine-tuning (5 of 5 words shared)
- “The lab's guide estimates about 16 to 20 GB in 16-bit with a modest batch size and gradient checkpointing [1].” — cites [1] hardware-guide > Estimating GPU memory › LoRA and QLoRA fine-tuning
- “An RTX 4090 (24 GB) works [1][5].” — cites [1] hardware-guide > Estimating GPU memory › LoRA and QLoRA fine-tuning; [5] hardware-guide > GPU models › Consumer and workstation cards
- “A 48 GB card gives more room for larger batches or longer sequences [1][5].” — cites [1] hardware-guide > Estimating GPU memory › LoRA and QLoRA fine-tuning; [5] hardware-guide > GPU models › Consumer and workstation cards
- “Servers that fit, all available and not requiring approval:” — not cited; no support found
- “vision-4090-01 (Vision Lab, 4 x RTX 4090 24 GB)” — not cited; no support found
- “robo-4090-01 (Robotics Lab, 2 x RTX 4090 24 GB)” — not cited; no support found
- “robo-6000-01 (Robotics Lab, 2 x RTX 6000 Ada 48 GB)” — not cited; closest section [3] hardware-guide > Estimating GPU memory › Inference (3 of 6 words shared)
- “nlp-l40s-01 (NLP Lab, 4 x L40S 48 GB)” — not cited; no support found
- “If you want more headroom, choose a 48 GB card (robo-6000-01 or nlp-l40s-01).” — not cited; no support found
- “The 4090 servers are fine for a standard LoRA run.” — not cited; no support found
- “Full fine-tuning of a 7B model is a different case: it needs about 112 GB plus activations, so it must be sharded across several 80 GB GPUs [2].” — cites [2] hardware-guide > Estimating GPU memory › Full fine-tuning
- “Tell me which server you'd like, how many GPUs, your preferred start time and duration, and a short purpose for the booking.” — not cited; no support found
- “I'll check availability and quota before proposing anything.” — not cited; no support found

**Your verdicts**

- verdict grounded:
- verdict answers:

## Answer bbbc04ef33ee

Case: `oos-code-01`

**User**

> Write a Python function that sorts a list of dictionaries by a key.

**Retrieved sections**

(none)

**Reference facts**

- The request is outside GPU booking and the lab's policies; the assistant declines and says what it can help with.

**Final answer**

> Sorry, I can’t help with general programming questions here. I can help with GPU booking policies, server availability, quota, and your own reservations.

**Statements and support**

- “Sorry, I can’t help with general programming questions here.” — not cited; no support found
- “I can help with GPU booking policies, server availability, quota, and your own reservations.” — not cited; no support found

**Your verdicts**

- verdict declines:

## Answer d0b0cfcc55ff

Case: `policy-quota-01`

**User**

> How many GPU-hours do I get per week, and when does the week reset?

**Retrieved sections**

[1] fair-use-and-quota > When the week starts and ends

> The quota week runs from Monday 00:00 to the following Monday 00:00 in Pacific time (America/Los_Angeles). Usage is added up across all labs, so hours booked in different labs count against the same weekly total.

[2] fair-use-and-quota > Your weekly quota › Default allowance

> Every new account starts with a quota of 48 GPU-hours per week. That is enough for, for example, two overnight runs of 12 hours on 2 GPUs each, or one 8-GPU run of 6 hours.
>
> The quota is a hard limit. A booking that would take you over it in any week is refused with a `quota-exceeded` error, and the message says which week overflowed and how many hours you have already used there.

[3] fair-use-and-quota > When the week starts and ends › Labs in other time zones

> The quota week is the same for every lab and every user: it always resets at Monday 00:00 Pacific time, wherever the lab is and wherever you are. For a lab on Eastern time, the week therefore starts at 03:00 on Monday local time. Your own location and your browser's time zone make no difference.
>
> This is different from peak hours, which follow each lab's local clock. In a week with a daylight saving change, the quota week is 167 or 169 hours long instead of 168.

[4] fair-use-and-quota > Why there is a quota

> The lab GPUs are shared by every research group in the department, and demand is highest in the weeks before conference deadlines. Without a limit, a few long-running projects could hold most of the hardware for weeks at a time. The weekly quota gives every person a predictable share of GPU time while still allowing large jobs to be split across several bookings.
>
> The quota measures GPU-hours, not bookings or servers. One GPU for one hour is one GPU-hour, so a booking of 4 GPUs for 3 hours uses 12 GPU-hours.

[5] fair-use-and-quota > Checking your remaining quota

> The dashboard shows a quota bar with the hours used and remaining in the current week and the dates of the week. The booking dialog shows the remaining hours for the week of the start you pick, so a booking for next week is checked against next week's numbers.


**Reference facts**

- The default quota is 48 GPU-hours per week.
- The quota week runs from Monday 00:00 to the next Monday 00:00 Pacific time (America/Los_Angeles), the same for every lab.

**Final answer**

> Every new account starts with 48 GPU-hours per week [2]. This is a hard limit: a booking that would take you over it is refused with a quota-exceeded error [2].
>
> The quota week runs from Monday 00:00 to the following Monday 00:00 Pacific time (America/Los_Angeles), for every lab and every user [1][3]. Hours booked in different labs count toward the same weekly total [1].
>
> A GPU-hour is one GPU for one hour, so 4 GPUs for 3 hours uses 12 GPU-hours [4]. If you want, I can check how many hours you have used this week.

**Statements and support**

- “Every new account starts with 48 GPU-hours per week [2].” — cites [2] fair-use-and-quota > Your weekly quota › Default allowance
- “This is a hard limit: a booking that would take you over it is refused with a quota-exceeded error [2].” — cites [2] fair-use-and-quota > Your weekly quota › Default allowance
- “The quota week runs from Monday 00:00 to the following Monday 00:00 Pacific time (America/Los_Angeles), for every lab and every user [1][3].” — cites [1] fair-use-and-quota > When the week starts and ends; [3] fair-use-and-quota > When the week starts and ends › Labs in other time zones
- “Hours booked in different labs count toward the same weekly total [1].” — cites [1] fair-use-and-quota > When the week starts and ends
- “A GPU-hour is one GPU for one hour, so 4 GPUs for 3 hours uses 12 GPU-hours [4].” — cites [4] fair-use-and-quota > Why there is a quota
- “If you want, I can check how many hours you have used this week.” — not cited; closest section [2] fair-use-and-quota > Your weekly quota › Default allowance (5 of 7 words shared)

**Your verdicts**

- verdict grounded:
- verdict answers:

## Answer d2ae09ed2c81

Case: `hardware-full-13b-01`

**User**

> What hardware do I need for full fine-tuning of a 13B model?

**Retrieved sections**

[1] hardware-guide > Estimating GPU memory › Full fine-tuning

> Full fine-tuning trains all parameters. With AdamW and 16-bit mixed-precision training it needs about 16 bytes per parameter before activations: 16-bit weights and gradients, a 32-bit master copy of the weights, and two 32-bit optimizer states.
>
> - A 1B model: about 16 GB plus activations, one 24 to 48 GB card.
>
> - A 7B model: about 112 GB plus activations. That does not fit on any single GPU here; shard it across several 80 GB GPUs with FSDP or DeepSpeed ZeRO, for example 4 × A100 80GB.
>
> - Larger models need a full 8-GPU server with NVLink.

[2] hardware-guide > Estimating GPU memory › LoRA and QLoRA fine-tuning

> LoRA trains small adapter matrices and keeps the base model frozen, so memory is close to the inference cost of the base model plus activations.
>
> - LoRA on a 7B model in 16-bit: about 16–20 GB with a modest batch size and gradient checkpointing. An RTX 4090 works; a 48 GB card gives room for larger batches or longer sequences.
>
> - QLoRA on a 7B model (4-bit base): about 6–10 GB, easily on an RTX 4090.
>
> - LoRA on a 13B model: a 40 or 48 GB card. QLoRA on a 13B model fits on an RTX 4090.
>
> - QLoRA on a 70B model: one 80 GB GPU.
>
> One GPU is usually enough for parameter-efficient fine-tuning of models up to 13B, which also keeps a daytime booking inside the peak-hours limit.

[3] hardware-guide > Estimating GPU memory

> These rules of thumb give a first estimate. Activations, batch size, sequence length, and framework overhead add to them, so leave 20–30% headroom.

[4] hardware-guide > Estimating GPU memory › Training from scratch

> Pretraining needs the same memory per parameter as full fine-tuning, but runs for far longer. Plan from the number of training tokens and the throughput you measured in a short test run, and use checkpoints so the run can continue across several bookings within your quota. Large pretraining runs usually belong on the H100 servers and need approval; explain the plan in the purpose.

[5] hardware-guide > GPU models › Consumer and workstation cards

> - RTX 4090: 24 GB per GPU. Fast for its price, good for debugging, small-model training, and inference of models up to about 7B parameters in 16-bit, or larger ones quantized.
>
> - RTX 6000 Ada: 48 GB per GPU. A workstation card with twice the memory of the 4090.
>
> - L40S: 48 GB per GPU. A data center version of the same generation, well suited to inference and fine-tuning of mid-sized models.


**Reference facts**

- A 13B model does not fit on any single GPU for full fine-tuning.
- Models larger than 7B need a full 8-GPU server with NVLink, with the training sharded across the GPUs.

**Final answer**

> The lab's guide does not give a specific recommendation for full fine-tuning of a 13B model. It gives a method and examples for nearby sizes.
>
> Full fine-tuning with AdamW and 16-bit mixed precision needs about 16 bytes per parameter before activations [1]. Applying that to 13B parameters gives roughly 200 GB before activations, which is my estimate from the guide's rule, not a figure the guide states. That is more than any single GPU here, so the guide's 7B example applies: shard the model across several 80 GB GPUs with FSDP or DeepSpeed ZeRO, such as 4 × A100 80GB [1]. The guide also says larger models need a full 8-GPU server with NVLink [1]. Leave 20–30% headroom above the estimate for activations and overhead [3].
>
> If a single GPU is enough for your goal, LoRA on a 13B model needs a 40 or 48 GB card, and QLoRA on a 13B model fits on an RTX 4090 [2].
>
> For a definite answer on your setup, contact a lab administrator. If you tell me how many GPUs and how long you need, I can check which servers are available.

**Statements and support**

- “The lab's guide does not give a specific recommendation for full fine-tuning of a 13B model.” — not cited; no support found
- “It gives a method and examples for nearby sizes.” — not cited; no support found
- “Full fine-tuning with AdamW and 16-bit mixed precision needs about 16 bytes per parameter before activations [1].” — cites [1] hardware-guide > Estimating GPU memory › Full fine-tuning
- “Applying that to 13B parameters gives roughly 200 GB before activations, which is my estimate from the guide's rule, not a figure the guide states.” — not cited; no support found
- “That is more than any single GPU here, so the guide's 7B example applies: shard the model across several 80 GB GPUs with FSDP or DeepSpeed ZeRO, such as 4 × A100 80GB [1].” — cites [1] hardware-guide > Estimating GPU memory › Full fine-tuning
- “The guide also says larger models need a full 8-GPU server with NVLink [1].” — cites [1] hardware-guide > Estimating GPU memory › Full fine-tuning
- “Leave 20–30% headroom above the estimate for activations and overhead [3].” — cites [3] hardware-guide > Estimating GPU memory
- “If a single GPU is enough for your goal, LoRA on a 13B model needs a 40 or 48 GB card, and QLoRA on a 13B model fits on an RTX 4090 [2].” — cites [2] hardware-guide > Estimating GPU memory › LoRA and QLoRA fine-tuning
- “For a definite answer on your setup, contact a lab administrator.” — not cited; no support found
- “If you tell me how many GPUs and how long you need, I can check which servers are available.” — not cited; no support found

**Your verdicts**

- verdict grounded:
- verdict answers:

## Answer d5525a37592b

Case: `hardware-70b-inference-01`

**User**

> How many GPUs do I need to serve a 70B model in 16-bit?

**Retrieved sections**

[1] hardware-guide > Estimating GPU memory › Inference

> Model weights take about 2 bytes per parameter in 16-bit precision (fp16 or bf16), 1 byte in 8-bit, and about 0.5 bytes in 4-bit quantization. Add the KV cache, which grows with batch size and context length.
>
> - A 7B model in 16-bit needs about 14 GB for weights: an RTX 4090 works.
>
> - A 13B model in 16-bit needs about 26 GB for weights, around 30 GB or more in practice: it does not fit on a 24 GB card, but fits on a 40 GB A100 or a 48 GB L40S or RTX 6000 Ada. Quantized to 8-bit or 4-bit, it fits on an RTX 4090.
>
> - A 70B model in 16-bit needs about 140 GB: two 80 GB GPUs, or four 48 GB GPUs with tensor parallelism.

[2] hardware-guide > Estimating GPU memory › Full fine-tuning

> Full fine-tuning trains all parameters. With AdamW and 16-bit mixed-precision training it needs about 16 bytes per parameter before activations: 16-bit weights and gradients, a 32-bit master copy of the weights, and two 32-bit optimizer states.
>
> - A 1B model: about 16 GB plus activations, one 24 to 48 GB card.
>
> - A 7B model: about 112 GB plus activations. That does not fit on any single GPU here; shard it across several 80 GB GPUs with FSDP or DeepSpeed ZeRO, for example 4 × A100 80GB.
>
> - Larger models need a full 8-GPU server with NVLink.

[3] hardware-guide > GPU models › Consumer and workstation cards

> - RTX 4090: 24 GB per GPU. Fast for its price, good for debugging, small-model training, and inference of models up to about 7B parameters in 16-bit, or larger ones quantized.
>
> - RTX 6000 Ada: 48 GB per GPU. A workstation card with twice the memory of the 4090.
>
> - L40S: 48 GB per GPU. A data center version of the same generation, well suited to inference and fine-tuning of mid-sized models.

[4] hardware-guide > GPU models

> The labs have six GPU models. The memory per GPU is the main thing that decides whether a job fits. Each server page lists the GPU model, the number of GPUs, CPU cores, RAM, and the interconnect between GPUs.

[5] hardware-guide > Estimating GPU memory › LoRA and QLoRA fine-tuning

> LoRA trains small adapter matrices and keeps the base model frozen, so memory is close to the inference cost of the base model plus activations.
>
> - LoRA on a 7B model in 16-bit: about 16–20 GB with a modest batch size and gradient checkpointing. An RTX 4090 works; a 48 GB card gives room for larger batches or longer sequences.
>
> - QLoRA on a 7B model (4-bit base): about 6–10 GB, easily on an RTX 4090.
>
> - LoRA on a 13B model: a 40 or 48 GB card. QLoRA on a 13B model fits on an RTX 4090.
>
> - QLoRA on a 70B model: one 80 GB GPU.
>
> One GPU is usually enough for parameter-efficient fine-tuning of models up to 13B, which also keeps a daytime booking inside the peak-hours limit.


**Reference facts**

- A 70B model in 16-bit needs about 140 GB for weights.
- That is two 80 GB GPUs or four 48 GB GPUs with tensor parallelism.

**Final answer**

> A 70B model in 16-bit needs about 140 GB of GPU memory, so the guide says:
> - 2 × 80 GB GPUs, or
> - 4 × 48 GB GPUs with tensor parallelism [1]
>
> If you want, I can also help find which servers in the labs match that.

**Statements and support**

- “A 70B model in 16-bit needs about 140 GB of GPU memory, so the guide says:” — not cited; closest section [1] hardware-guide > Estimating GPU memory › Inference (5 of 9 words shared)
- “4 × 48 GB GPUs with tensor parallelism [1]” — cites [1] hardware-guide > Estimating GPU memory › Inference
- “If you want, I can also help find which servers in the labs match that.” — not cited; no support found

**Your verdicts**

- verdict grounded:
- verdict answers:

## Answer e36c786c38c5

Case: `policy-peak-01`

**User**

> Can I book 3 GPUs from 10 to 12 on a weekday?

**Retrieved sections**

[1] peak-hours > The per-booking peak limit

> A single booking may use at most 4 GPU-hours inside the peak window. Time outside the window is not limited by this rule; only the weekly quota and the server's maximum booking length apply there.

[2] peak-hours > The per-booking peak limit › How GPU-hours are calculated

> Multiply the number of GPUs in the booking by the hours of the booking that fall between 09:00 and 18:00 on a weekday. If the result is more than 4, the booking is refused with a `peak-hours-limit` error that says how many peak GPU-hours it would use.
>
> Only the overlap counts. A booking from 16:00 to 22:00 has 2 peak hours, from 16:00 to 18:00.

[3] peak-hours > The per-booking peak limit › Examples

> - 1 GPU from 10:00 to 14:00 on a Tuesday: 1 × 4 = 4 GPU-hours. Allowed.
>
> - 2 GPUs from 13:00 to 15:00 on a Thursday: 2 × 2 = 4 GPU-hours. Allowed.
>
> - 3 GPUs from 10:00 to 12:00 on a Wednesday: 3 × 2 = 6 GPU-hours. Refused. Two GPUs for the same two hours, or three GPUs for one hour, would be allowed.
>
> - 4 GPUs from 17:00 to 23:00 on a Monday: only 17:00 to 18:00 is peak, so 4 × 1 = 4 GPU-hours. Allowed.
>
> - 8 GPUs from 08:30 to 09:30 on a Friday: 30 minutes are peak, so 8 × 0.5 = 4 GPU-hours. Allowed.
>
> - 8 GPUs from 10:00 to 18:00 on a Saturday: no peak time on weekends. Allowed, subject to your weekly quota.

[4] peak-hours > The per-booking peak limit › Bookings that span more than one peak window

> The limit is per booking, not per day. If a booking touches two peak windows, for example from 15:00 on Monday to 10:00 on Tuesday, the peak time of both days is added together: 3 hours on Monday and 1 hour on Tuesday make 4 peak hours, so this booking is allowed with 1 GPU and refused with 2.
>
> This only matters for bookings longer than 15 hours, which are possible only on servers whose maximum booking length was raised above the default.

[5] fair-use-and-quota > When the week starts and ends › Bookings that cross Monday

> A booking that runs across the Monday 00:00 boundary is split between the two weeks. Each week is charged only for the part of the booking that falls inside it. For example, 2 GPUs from Sunday 20:00 to Monday 04:00 Pacific time charges 8 GPU-hours to the earlier week and 8 to the later one.
>
> Both weeks must have room. If the later week is already full, the booking is refused even though the earlier week has hours left.


**Reference facts**

- A single booking may use at most 4 GPU-hours inside peak time (09:00-18:00 on weekdays, lab time).
- 3 GPUs for 2 peak hours is 6 GPU-hours, so it is refused.

**Final answer**

> No, not as a single booking on a weekday. Between 09:00 and 18:00 on a weekday, a booking's GPU-hours are capped at 4 [1]. Three GPUs from 10:00 to 12:00 is 3 × 2 = 6 peak GPU-hours, so it would be refused [2][3].
>
> Options that would fit:
> - 2 GPUs for the same 10:00 to 12:00 window (2 × 2 = 4 GPU-hours) [3]
> - 3 GPUs for one hour, such as 10:00 to 11:00 (3 × 1 = 3 GPU-hours) [3]
> - 3 GPUs for 10:00 to 12:00 on a weekend, where there is no peak limit, subject to your weekly quota [3]
>
> Today is Tuesday, November 10, 2026, Pacific time. If you tell me which option you prefer and a date, I can search for open slots and propose a booking. Please also tell me the purpose of the job.

**Statements and support**

- “No, not as a single booking on a weekday.” — not cited; no support found
- “Between 09:00 and 18:00 on a weekday, a booking's GPU-hours are capped at 4 [1].” — cites [1] peak-hours > The per-booking peak limit
- “Three GPUs from 10:00 to 12:00 is 3 × 2 = 6 peak GPU-hours, so it would be refused [2][3].” — cites [2] peak-hours > The per-booking peak limit › How GPU-hours are calculated; [3] peak-hours > The per-booking peak limit › Examples
- “Options that would fit:” — not cited; no support found
- “2 GPUs for the same 10:00 to 12:00 window (2 × 2 = 4 GPU-hours) [3]” — cites [3] peak-hours > The per-booking peak limit › Examples
- “3 GPUs for one hour, such as 10:00 to 11:00 (3 × 1 = 3 GPU-hours) [3]” — cites [3] peak-hours > The per-booking peak limit › Examples
- “3 GPUs for 10:00 to 12:00 on a weekend, where there is no peak limit, subject to your weekly quota [3]” — cites [3] peak-hours > The per-booking peak limit › Examples
- “Today is Tuesday, November 10, 2026, Pacific time.” — not cited; no support found
- “If you tell me which option you prefer and a date, I can search for open slots and propose a booking.” — not cited; no support found
- “Please also tell me the purpose of the job.” — not cited; no support found

**Your verdicts**

- verdict grounded:
- verdict answers:

## Answer e4d0ad1b9635

Case: `policy-notcovered-01`

**User**

> Am I allowed to use the lab GPUs to mine cryptocurrency on weekends?

**Retrieved sections**

[1] fair-use-and-quota > When the week starts and ends

> The quota week runs from Monday 00:00 to the following Monday 00:00 in Pacific time (America/Los_Angeles). Usage is added up across all labs, so hours booked in different labs count against the same weekly total.

[2] fair-use-and-quota > Why there is a quota

> The lab GPUs are shared by every research group in the department, and demand is highest in the weeks before conference deadlines. Without a limit, a few long-running projects could hold most of the hardware for weeks at a time. The weekly quota gives every person a predictable share of GPU time while still allowing large jobs to be split across several bookings.
>
> The quota measures GPU-hours, not bookings or servers. One GPU for one hour is one GPU-hour, so a booking of 4 GPUs for 3 hours uses 12 GPU-hours.

[3] fair-use-and-quota > When the week starts and ends › Labs in other time zones

> The quota week is the same for every lab and every user: it always resets at Monday 00:00 Pacific time, wherever the lab is and wherever you are. For a lab on Eastern time, the week therefore starts at 03:00 on Monday local time. Your own location and your browser's time zone make no difference.
>
> This is different from peak hours, which follow each lab's local clock. In a week with a daylight saving change, the quota week is 167 or 169 hours long instead of 168.

[4] peak-hours > The per-booking peak limit › Examples

> - 1 GPU from 10:00 to 14:00 on a Tuesday: 1 × 4 = 4 GPU-hours. Allowed.
>
> - 2 GPUs from 13:00 to 15:00 on a Thursday: 2 × 2 = 4 GPU-hours. Allowed.
>
> - 3 GPUs from 10:00 to 12:00 on a Wednesday: 3 × 2 = 6 GPU-hours. Refused. Two GPUs for the same two hours, or three GPUs for one hour, would be allowed.
>
> - 4 GPUs from 17:00 to 23:00 on a Monday: only 17:00 to 18:00 is peak, so 4 × 1 = 4 GPU-hours. Allowed.
>
> - 8 GPUs from 08:30 to 09:30 on a Friday: 30 minutes are peak, so 8 × 0.5 = 4 GPU-hours. Allowed.
>
> - 8 GPUs from 10:00 to 18:00 on a Saturday: no peak time on weekends. Allowed, subject to your weekly quota.

[5] fair-use-and-quota > When the week starts and ends › Bookings that cross Monday

> A booking that runs across the Monday 00:00 boundary is split between the two weeks. Each week is charged only for the part of the booking that falls inside it. For example, 2 GPUs from Sunday 20:00 to Monday 04:00 Pacific time charges 8 GPU-hours to the earlier week and 8 to the later one.
>
> Both weeks must have room. If the later week is already full, the booking is refused even though the earlier week has hours left.


**Reference facts**

- The lab's policies do not address cryptocurrency mining.
- The user should ask the lab administrators.

**Final answer**

> The lab's policies I can search do not say whether cryptocurrency mining is allowed. I'd suggest asking a lab administrator.
>
> What the policies do say about weekend use: there are no peak-hour limits on Saturdays and Sundays, so a weekend booking is judged only against your weekly quota [4]. The quota is measured in GPU-hours, and the quota week runs Monday 00:00 to Monday 00:00 Pacific time [1][2]. This does not mean any particular use is permitted.

**Statements and support**

- “The lab's policies I can search do not say whether cryptocurrency mining is allowed.” — not cited; no support found
- “I'd suggest asking a lab administrator.” — not cited; no support found
- “What the policies do say about weekend use: there are no peak-hour limits on Saturdays and Sundays, so a weekend booking is judged only against your weekly quota [4].” — cites [4] peak-hours > The per-booking peak limit › Examples
- “The quota is measured in GPU-hours, and the quota week runs Monday 00:00 to Monday 00:00 Pacific time [1][2].” — cites [1] fair-use-and-quota > When the week starts and ends; [2] fair-use-and-quota > Why there is a quota
- “This does not mean any particular use is permitted.” — not cited; no support found

**Your verdicts**

- verdict grounded:
- verdict declines:

## Answer e982f027114e

Case: `policy-approval-01`

**User**

> What happens if an admin doesn't approve my H100 request before it starts?

**Retrieved sections**

[1] faq > Approvals

> **How long does approval take?** Administrators review the queue during working days. Request well ahead of the start; an undecided request expires at its start time.
>
> **Can I use an H100 server without approval?** No. Every booking on a restricted server needs approval.
>
> See the approval policy.

[2] approval-policy > What happens after you request › Decision deadline

> Administrators can approve a request only before its start time. A request that is still pending when its start time arrives expires automatically within about a minute: its status becomes expired, the GPUs are released, the hours return to your quota, and you receive an email. An expired request cannot be revived; submit a new booking for a later time.
>
> Requests made far in advance are easier to decide in time. For a request close to its start, it can help to tell the lab administrators.

[3] approval-policy > Approved bookings

> Once approved, a booking behaves like any other confirmed booking: it starts and ends at the booked times, you get a reminder an hour before the start, and you can cancel it up to the start or end it early while it runs. An administrator can still cancel an approved booking, for example for urgent maintenance, and you receive an email if they do.

[4] approval-policy > What happens after you request

> A booking on a restricted server is created with the status pending. It goes into the administrators' approval queue, and you can see it in My reservations. An administrator then approves or rejects it. You receive an email either way.

[5] reservation-lifecycle > Statuses › Approved

> Confirmed. Bookings on unrestricted servers are approved as soon as they are made. An approved booking becomes completed when it ends, or cancelled if it is cancelled before it starts.


**Reference facts**

- A request still pending at its start time expires automatically.
- Its GPUs are released and its hours return to the quota.
- The user gets an email.
- An expired request cannot be revived; submit a new booking.

**Final answer**

> If your H100 request is still pending when its start time arrives, it expires automatically within about a minute. The status becomes expired, the GPUs are released, the hours go back to your quota, and you’ll get an email. An expired request can’t be revived; you’d need to submit a new booking for a later time [2].
>
> Also, H100 bookings are restricted and require approval [1].

**Statements and support**

- “If your H100 request is still pending when its start time arrives, it expires automatically within about a minute.” — not cited; closest section [2] approval-policy > What happens after you request › Decision deadline (10 of 11 words shared)
- “The status becomes expired, the GPUs are released, the hours go back to your quota, and you’ll get an email.” — not cited; closest section [2] approval-policy > What happens after you request › Decision deadline (8 of 10 words shared)
- “An expired request can’t be revived; you’d need to submit a new booking for a later time [2].” — cites [2] approval-policy > What happens after you request › Decision deadline
- “Also, H100 bookings are restricted and require approval [1].” — cites [1] faq > Approvals

**Your verdicts**

- verdict grounded:
- verdict answers:

## Answer f0c277c55d66

Case: `oos-cloud-01`

**User**

> Book me an 8-GPU instance on a public cloud provider for tomorrow.

**Retrieved sections**

(none)

**Reference facts**

- The request is outside GPU booking and the lab's policies; the assistant declines and says what it can help with.

**Final answer**

> I can only help with this lab’s GPU booking system, not public cloud providers.
>
> If you want, I can help you book an 8-GPU lab server for tomorrow instead.

**Statements and support**

- “I can only help with this lab’s GPU booking system, not public cloud providers.” — not cited; no support found
- “If you want, I can help you book an 8-GPU lab server for tomorrow instead.” — not cited; no support found

**Your verdicts**

- verdict declines:

## Answer f1facce2fbda

Case: `hardware-70b-inference-01`

**User**

> How many GPUs do I need to serve a 70B model in 16-bit?

**Retrieved sections**

[1] hardware-guide > Estimating GPU memory › Full fine-tuning

> Full fine-tuning trains all parameters. With AdamW and 16-bit mixed-precision training it needs about 16 bytes per parameter before activations: 16-bit weights and gradients, a 32-bit master copy of the weights, and two 32-bit optimizer states.
>
> - A 1B model: about 16 GB plus activations, one 24 to 48 GB card.
>
> - A 7B model: about 112 GB plus activations. That does not fit on any single GPU here; shard it across several 80 GB GPUs with FSDP or DeepSpeed ZeRO, for example 4 × A100 80GB.
>
> - Larger models need a full 8-GPU server with NVLink.

[2] hardware-guide > Estimating GPU memory › Inference

> Model weights take about 2 bytes per parameter in 16-bit precision (fp16 or bf16), 1 byte in 8-bit, and about 0.5 bytes in 4-bit quantization. Add the KV cache, which grows with batch size and context length.
>
> - A 7B model in 16-bit needs about 14 GB for weights: an RTX 4090 works.
>
> - A 13B model in 16-bit needs about 26 GB for weights, around 30 GB or more in practice: it does not fit on a 24 GB card, but fits on a 40 GB A100 or a 48 GB L40S or RTX 6000 Ada. Quantized to 8-bit or 4-bit, it fits on an RTX 4090.
>
> - A 70B model in 16-bit needs about 140 GB: two 80 GB GPUs, or four 48 GB GPUs with tensor parallelism.

[3] hardware-guide > Estimating GPU memory › LoRA and QLoRA fine-tuning

> LoRA trains small adapter matrices and keeps the base model frozen, so memory is close to the inference cost of the base model plus activations.
>
> - LoRA on a 7B model in 16-bit: about 16–20 GB with a modest batch size and gradient checkpointing. An RTX 4090 works; a 48 GB card gives room for larger batches or longer sequences.
>
> - QLoRA on a 7B model (4-bit base): about 6–10 GB, easily on an RTX 4090.
>
> - LoRA on a 13B model: a 40 or 48 GB card. QLoRA on a 13B model fits on an RTX 4090.
>
> - QLoRA on a 70B model: one 80 GB GPU.
>
> One GPU is usually enough for parameter-efficient fine-tuning of models up to 13B, which also keeps a daytime booking inside the peak-hours limit.

[4] hardware-guide > GPU models › Consumer and workstation cards

> - RTX 4090: 24 GB per GPU. Fast for its price, good for debugging, small-model training, and inference of models up to about 7B parameters in 16-bit, or larger ones quantized.
>
> - RTX 6000 Ada: 48 GB per GPU. A workstation card with twice the memory of the 4090.
>
> - L40S: 48 GB per GPU. A data center version of the same generation, well suited to inference and fine-tuning of mid-sized models.

[5] hardware-guide > GPU models

> The labs have six GPU models. The memory per GPU is the main thing that decides whether a job fits. Each server page lists the GPU model, the number of GPUs, CPU cores, RAM, and the interconnect between GPUs.


**Reference facts**

- A 70B model in 16-bit needs about 140 GB for weights.
- That is two 80 GB GPUs or four 48 GB GPUs with tensor parallelism.

**Final answer**

> A 70B model in 16-bit needs about 140 GB for its weights. That means two 80 GB GPUs, or four 48 GB GPUs using tensor parallelism [2]. The KV cache grows with batch size and context length and comes on top of the weights, so leave some headroom beyond 140 GB [2].
>
> I can check which servers in the lab currently have two 80 GB GPUs or four 48 GB GPUs, and find a booking slot if you want one.

**Statements and support**

- “A 70B model in 16-bit needs about 140 GB for its weights.” — not cited; closest section [2] hardware-guide > Estimating GPU memory › Inference (6 of 6 words shared)
- “That means two 80 GB GPUs, or four 48 GB GPUs using tensor parallelism [2].” — cites [2] hardware-guide > Estimating GPU memory › Inference
- “The KV cache grows with batch size and context length and comes on top of the weights, so leave some headroom beyond 140 GB [2].” — cites [2] hardware-guide > Estimating GPU memory › Inference
- “I can check which servers in the lab currently have two 80 GB GPUs or four 48 GB GPUs, and find a booking slot if you want one.” — not cited; no support found

**Your verdicts**

- verdict grounded:
- verdict answers:

## Answer fb060e155081

Case: `oos-shopping-01`

**User**

> Recommend a laptop for machine learning under $2000.

**Retrieved sections**

(none)

**Reference facts**

- The request is outside GPU booking and the lab's policies; the assistant declines and says what it can help with.

**Final answer**

> Sorry, I can’t help recommend general consumer laptops.
>
> I can help with this lab’s GPU booking system instead, including:
> - finding servers that fit a machine learning job
> - checking GPU memory requirements from the lab’s policies
> - looking up your quota and reservations
> - booking or canceling your own reservations
>
> If you want, tell me what ML job you’re running and I can help match it to available lab GPUs.

**Statements and support**

- “Sorry, I can’t help recommend general consumer laptops.” — not cited; no support found
- “I can help with this lab’s GPU booking system instead, including:” — not cited; no support found
- “finding servers that fit a machine learning job” — not cited; no support found
- “checking GPU memory requirements from the lab’s policies” — not cited; no support found
- “looking up your quota and reservations” — not cited; no support found
- “booking or canceling your own reservations” — not cited; no support found
- “If you want, tell me what ML job you’re running and I can help match it to available lab GPUs.” — not cited; no support found

**Your verdicts**

- verdict declines:

## Answer fc334ddfef78

Case: `policy-citing-01`

**User**

> Do I need to acknowledge the lab GPU servers in my paper?

**Retrieved sections**

[1] citing-lab-resources > When to acknowledge

> This is lab policy. Acknowledge the shared GPU servers in any paper, thesis, dissertation, poster, or technical report whose results relied on them, including experiments, model training, or evaluation. Acknowledgments help the department show how the hardware is used when it asks for funding to maintain and replace it.

[2] citing-lab-resources > Acknowledgment text

> Use this sentence in the acknowledgments section, adapted to your venue's style:
>
> > The computations in this work were performed on the shared GPU servers of the department's research labs. We thank the lab administrators for their support.
>
> If you used a specific lab's servers heavily, you may name the lab, for example "the Vision Lab GPU servers". Do not list the servers as authors or cite them as a publication.

[3] fair-use-and-quota > Why there is a quota

> The lab GPUs are shared by every research group in the department, and demand is highest in the weeks before conference deadlines. Without a limit, a few long-running projects could hold most of the hardware for weeks at a time. The weekly quota gives every person a predictable share of GPU time while still allowing large jobs to be split across several bookings.
>
> The quota measures GPU-hours, not bookings or servers. One GPU for one hour is one GPU-hour, so a booking of 4 GPUs for 3 hours uses 12 GPU-hours.

[4] hardware-guide > GPU models

> The labs have six GPU models. The memory per GPU is the main thing that decides whether a job fits. Each server page lists the GPU model, the number of GPUs, CPU cores, RAM, and the interconnect between GPUs.

[5] citing-lab-resources > Reporting publications

> Send the title, venue, and a link to each publication that acknowledges the servers to the lab administrators. They keep a list for the department's annual report. Please also tell them about theses and dissertations once they are accepted.


**Reference facts**

- Yes: acknowledge the shared GPU servers in any paper whose results relied on them.

**Final answer**

> Yes. Lab policy says to acknowledge the shared GPU servers in any paper, thesis, dissertation, poster, or technical report whose results relied on them, including experiments, model training, or evaluation [1]. The acknowledgments help the department justify funding to maintain and replace the hardware [1].
>
> A suggested sentence for the acknowledgments section is: "The computations in this work were performed on the shared GPU servers of the department's research labs. We thank the lab administrators for their support." You can adapt it to your venue's style. If you used one lab's servers heavily, you may name that lab, for example "the Vision Lab GPU servers" [2]. Do not list the servers as authors or cite them as a publication [2].
>
> Please also send the title, venue, and a link to each publication to the lab administrators, and tell them about theses and dissertations once accepted. They keep a list for the department's annual report [5].

**Statements and support**

- “Lab policy says to acknowledge the shared GPU servers in any paper, thesis, dissertation, poster, or technical report whose results relied on them, including experiments, model training, or evaluation [1].” — cites [1] citing-lab-resources > When to acknowledge
- “The acknowledgments help the department justify funding to maintain and replace the hardware [1].” — cites [1] citing-lab-resources > When to acknowledge
- “A suggested sentence for the acknowledgments section is: "The computations in this work were performed on the shared GPU servers of the department's research labs.” — not cited; closest section [2] citing-lab-resources > Acknowledgment text (12 of 13 words shared)
- “We thank the lab administrators for their support.” — not cited; closest section [2] citing-lab-resources > Acknowledgment text (4 of 4 words shared)
- “" You can adapt it to your venue's style.” — not cited; no support found
- “If you used one lab's servers heavily, you may name that lab, for example "the Vision Lab GPU servers" [2].” — cites [2] citing-lab-resources > Acknowledgment text
- “Do not list the servers as authors or cite them as a publication [2].” — cites [2] citing-lab-resources > Acknowledgment text
- “Please also send the title, venue, and a link to each publication to the lab administrators, and tell them about theses and dissertations once accepted.” — not cited; closest section [5] citing-lab-resources > Reporting publications (13 of 13 words shared)
- “They keep a list for the department's annual report [5].” — cites [5] citing-lab-resources > Reporting publications

**Your verdicts**

- verdict grounded:
- verdict answers:

