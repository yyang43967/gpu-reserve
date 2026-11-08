import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, status
from pydantic import AwareDatetime

from app.api import problem_responses
from app.api.deps import AdminUser, CurrentUser, SessionDep, SettingsDep
from app.schemas.stats import OverviewRead, UtilizationRead
from app.services import stats

router = APIRouter(
    prefix="/stats",
    tags=["stats"],
    responses=problem_responses(status.HTTP_401_UNAUTHORIZED),
)


@router.get("/overview")
async def get_overview(
    user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> OverviewRead:
    """Dashboard numbers: the caller's quota this week, next three bookings, and pending
    requests. Admins also get the approval queue size and how busy the GPUs are."""
    overview = await stats.overview(
        session, user, now=datetime.now(UTC), quota_zone=settings.quota_zone
    )
    return OverviewRead.build(overview)


@router.get(
    "/utilization",
    responses=problem_responses(
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
    ),
)
async def get_utilization(
    user: AdminUser,
    session: SessionDep,
    lab_id: uuid.UUID | None = None,
    starts_at: Annotated[
        AwareDatetime | None,
        Query(alias="from", description="Window start; defaults to seven days before `to`."),
    ] = None,
    ends_at: Annotated[
        AwareDatetime | None, Query(alias="to", description="Window end; defaults to now.")
    ] = None,
) -> UtilizationRead:
    """GPU-hours booked (approved and completed), pending, and available per server
    that is not retired. The window is at most 31 days."""
    window = stats.utilization_window(starts_at, ends_at, now=datetime.now(UTC))
    report = await stats.utilization(session, user, window=window, lab_id=lab_id)
    return UtilizationRead.build(report)
