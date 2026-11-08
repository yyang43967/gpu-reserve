import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel

from app.models import GpuModel, ServerStatus
from app.schemas.reservation import QuotaRead, ReservationLab, ReservationRead
from app.services.stats import AdminOverview, Overview, Utilization, UtilizationTotals


class AdminOverviewRead(BaseModel):
    pending_approvals: int
    gpus_in_use: int
    gpus_total: int
    week_start: datetime
    week_end: datetime
    # Booked GPU-hours over available GPU-hours this quota week, from 0 to 1.
    utilization_this_week: float

    @classmethod
    def build(cls, admin: AdminOverview) -> Self:
        return cls(
            pending_approvals=admin.pending_approvals,
            gpus_in_use=admin.gpus_in_use,
            gpus_total=admin.gpus_total,
            week_start=admin.week.start,
            week_end=admin.week.end,
            utilization_this_week=admin.utilization_this_week,
        )


class OverviewRead(BaseModel):
    quota: QuotaRead
    upcoming: list[ReservationRead]
    my_pending: int
    # Present for admins only.
    admin: AdminOverviewRead | None

    @classmethod
    def build(cls, overview: Overview) -> Self:
        return cls(
            quota=QuotaRead.build(overview.quota),
            upcoming=[ReservationRead.build(r) for r in overview.upcoming],
            my_pending=overview.my_pending,
            admin=AdminOverviewRead.build(overview.admin) if overview.admin else None,
        )


class UtilizationTotalsRead(BaseModel):
    available_gpu_hours: float
    booked_gpu_hours: float
    pending_gpu_hours: float
    # booked / available, from 0 to 1.
    utilization: float

    @classmethod
    def from_totals(cls, totals: UtilizationTotals) -> Self:
        return cls(
            available_gpu_hours=totals.available_gpu_hours,
            booked_gpu_hours=totals.booked_gpu_hours,
            pending_gpu_hours=totals.pending_gpu_hours,
            utilization=totals.utilization,
        )


class ServerUtilizationRead(UtilizationTotalsRead):
    server_id: uuid.UUID
    hostname: str
    gpu_model: GpuModel
    gpu_count: int
    status: ServerStatus
    lab: ReservationLab


class UtilizationRead(BaseModel):
    """GPU-hours per server that is not retired. Available hours use each server's
    current GPU count for the whole window."""

    starts_at: datetime
    ends_at: datetime
    servers: list[ServerUtilizationRead]
    totals: UtilizationTotalsRead

    @classmethod
    def build(cls, report: Utilization) -> Self:
        return cls(
            starts_at=report.window.start,
            ends_at=report.window.end,
            servers=[
                ServerUtilizationRead(
                    **UtilizationTotalsRead.from_totals(row).model_dump(),
                    server_id=row.server.id,
                    hostname=row.server.hostname,
                    gpu_model=row.server.gpu_model,
                    gpu_count=row.server.gpu_count,
                    status=row.server.status,
                    lab=ReservationLab(
                        id=row.server.lab.id,
                        name=row.server.lab.name,
                        timezone=row.server.lab.timezone,
                    ),
                )
                for row in report.servers
            ],
            totals=UtilizationTotalsRead.from_totals(report.totals),
        )
