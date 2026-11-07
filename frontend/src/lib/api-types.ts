// Shapes of API responses. Kept in sync by hand with the backend schemas.

export type UserRole = "student" | "admin";

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  weekly_gpu_hour_quota: number;
  is_active: boolean;
  avatar_key: string | null;
  /** A presigned download URL, or null without an avatar. */
  avatar_url: string | null;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
  /** Seconds until the access token expires. */
  expires_in: number;
  user: User;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface FieldError {
  field: string;
  message: string;
}

/** An RFC 7807 problem details document, as every API error is returned. */
export interface Problem {
  type: string;
  title: string;
  status: number;
  detail?: string;
  instance?: string;
  errors?: FieldError[];
}

export type GpuModel =
  "RTX_4090" | "RTX_6000_ADA" | "L40S" | "A100_40GB" | "A100_80GB" | "H100_80GB";

export type ServerStatus = "available" | "maintenance" | "retired";

export interface Lab {
  id: string;
  name: string;
  location: string;
  /** An IANA time zone name, such as America/Los_Angeles. */
  timezone: string;
  description: string | null;
  is_active: boolean;
  created_at: string;
}

/** The part of a lab embedded in each server. */
export interface LabSummary {
  id: string;
  name: string;
  timezone: string;
  is_active: boolean;
}

export interface Server {
  id: string;
  lab: LabSummary;
  hostname: string;
  gpu_model: GpuModel;
  /** Memory per GPU, derived from the GPU model. */
  gpu_memory_gb: number;
  gpu_count: number;
  cpu_cores: number;
  ram_gb: number;
  interconnect: string | null;
  status: ServerStatus;
  requires_approval: boolean;
  max_booking_hours: number;
  notes: string | null;
  /** A presigned download URL, or null without an image. */
  image_url: string | null;
  created_at: string;
}

export type UploadPurpose = "avatar" | "server_image";

/**
 * A short-lived form for one upload straight to storage: POST every entry of `fields`,
 * then the file as the last field, named `file`.
 */
export interface PresignResponse {
  key: string;
  url: string;
  fields: Record<string, string>;
  max_bytes: number;
  expires_at: string;
}

export type ReservationStatus =
  "pending" | "approved" | "rejected" | "cancelled" | "expired" | "completed";

export interface Reservation {
  id: string;
  status: ReservationStatus;
  gpu_count: number;
  starts_at: string;
  ends_at: string;
  purpose: string;
  created_via: "web" | "assistant";
  decided_at: string | null;
  decision_reason: string | null;
  created_at: string;
  server: { id: string; hostname: string; gpu_model: GpuModel };
  lab: { id: string; name: string; timezone: string };
  user: { id: string; full_name: string; email: string };
  /** Numbers of the GPUs this reservation holds or held. */
  gpu_indexes: number[];
}

/** A stretch of 30-minute slots with the same number of free GPUs. */
export interface FreeRun {
  starts_at: string;
  ends_at: string;
  free: number;
}

export interface Availability {
  server_id: string;
  /** The lab's zone, for display. */
  timezone: string;
  gpu_count: number;
  slot_minutes: number;
  starts_at: string;
  ends_at: string;
  runs: FreeRun[];
}

/** GPU-hours in one quota week (the current one unless another was asked for). Hours can be fractional. */
export interface Quota {
  week_start: string;
  week_end: string;
  timezone: string;
  used_hours: number;
  quota_hours: number;
  remaining_hours: number;
}
