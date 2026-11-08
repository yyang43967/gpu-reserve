---
title: Hardware guide
last_reviewed: 2026-11-08
---

# Hardware guide

## GPU models

The labs have six GPU models. The memory per GPU is the main thing that decides whether a job fits. Each server page lists the GPU model, the number of GPUs, CPU cores, RAM, and the interconnect between GPUs.

### Consumer and workstation cards

- RTX 4090: 24 GB per GPU. Fast for its price, good for debugging, small-model training, and inference of models up to about 7B parameters in 16-bit, or larger ones quantized.
- RTX 6000 Ada: 48 GB per GPU. A workstation card with twice the memory of the 4090.
- L40S: 48 GB per GPU. A data center version of the same generation, well suited to inference and fine-tuning of mid-sized models.

### Data center cards

- A100 40GB: 40 GB per GPU.
- A100 80GB: 80 GB per GPU.
- H100 80GB: 80 GB per GPU, the fastest model in the labs. Servers with H100 GPUs require administrator approval.

Some servers, mostly A100 and H100 ones, have NVLink between their GPUs, which matters for jobs that split one model across several GPUs.

## Estimating GPU memory

These rules of thumb give a first estimate. Activations, batch size, sequence length, and framework overhead add to them, so leave 20–30% headroom.

### Inference

Model weights take about 2 bytes per parameter in 16-bit precision (fp16 or bf16), 1 byte in 8-bit, and about 0.5 bytes in 4-bit quantization. Add the KV cache, which grows with batch size and context length.

- A 7B model in 16-bit needs about 14 GB for weights: an RTX 4090 works.
- A 13B model in 16-bit needs about 26 GB for weights, around 30 GB or more in practice: it does not fit on a 24 GB card, but fits on a 40 GB A100 or a 48 GB L40S or RTX 6000 Ada. Quantized to 8-bit or 4-bit, it fits on an RTX 4090.
- A 70B model in 16-bit needs about 140 GB: two 80 GB GPUs, or four 48 GB GPUs with tensor parallelism.

### LoRA and QLoRA fine-tuning

LoRA trains small adapter matrices and keeps the base model frozen, so memory is close to the inference cost of the base model plus activations.

- LoRA on a 7B model in 16-bit: about 16–20 GB with a modest batch size and gradient checkpointing. An RTX 4090 works; a 48 GB card gives room for larger batches or longer sequences.
- QLoRA on a 7B model (4-bit base): about 6–10 GB, easily on an RTX 4090.
- LoRA on a 13B model: a 40 or 48 GB card. QLoRA on a 13B model fits on an RTX 4090.
- QLoRA on a 70B model: one 80 GB GPU.

One GPU is usually enough for parameter-efficient fine-tuning of models up to 13B, which also keeps a daytime booking inside the peak-hours limit.

### Full fine-tuning

Full fine-tuning trains all parameters. With AdamW and 16-bit mixed-precision training it needs about 16 bytes per parameter before activations: 16-bit weights and gradients, a 32-bit master copy of the weights, and two 32-bit optimizer states.

- A 1B model: about 16 GB plus activations, one 24 to 48 GB card.
- A 7B model: about 112 GB plus activations. That does not fit on any single GPU here; shard it across several 80 GB GPUs with FSDP or DeepSpeed ZeRO, for example 4 × A100 80GB.
- Larger models need a full 8-GPU server with NVLink.

### Training from scratch

Pretraining needs the same memory per parameter as full fine-tuning, but runs for far longer. Plan from the number of training tokens and the throughput you measured in a short test run, and use checkpoints so the run can continue across several bookings within your quota. Large pretraining runs usually belong on the H100 servers and need approval; explain the plan in the purpose.

## Multi-GPU jobs

Book all GPUs of a job on one server; a booking cannot span servers. Data-parallel training of a model that fits on one GPU scales well on any server.

### When NVLink matters

NVLink is a direct high-bandwidth link between the GPUs of one server. It makes a large difference when GPUs exchange a lot of data at every step:

- tensor parallelism, which divides each layer's weight matrices among the GPUs;
- fully sharded training (FSDP, ZeRO stage 3) of large models;
- data-parallel training of large models, where the gradient all-reduce is big.

For data-parallel training of small models, and for running independent jobs on separate GPUs, GPUs connected over PCIe are fine. The server page shows whether a server has NVLink.

## Choosing a server

Use the server list filters (GPU model, minimum memory per GPU, minimum number of GPUs, lab) or "Find a slot", which searches all servers for a time that fits your job.

### Start with the smallest GPU that fits

Pick the smallest GPU memory that fits your job with headroom, and the fewest GPUs that give acceptable speed. Large GPUs are scarce, and leaving them free for jobs that need them keeps waiting times short for everyone. "Find a slot" ranks results this way: earliest start first, then servers that need no approval, then the smallest GPU memory that fits.

Run a short test on a small booking first to measure memory use and speed before booking a long run.
