import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, status
from pydantic import AwareDatetime

from app.api import problem_responses
from app.api.deps import AdminUser, CurrentUser, SessionDep, SettingsDep
from app.models import ReservationStatus
from app.schemas.pagination import Page, PageParamsDep
from app.schemas.reservation import ReservationCreate, ReservationRead, ReservationReject
from app.services import reservations

router = APIRouter(
    prefix="/reservations",
    tags=["reservations"],
    responses=problem_responses(status.HTTP_401_UNAUTHORIZED),
)

_ADMIN = problem_responses(status.HTTP_403_FORBIDDEN)
_NOT_FOUND = problem_responses(status.HTTP_404_NOT_FOUND)
_CONFLICT = problem_responses(status.HTTP_409_CONFLICT)
_INVALID = problem_responses(status.HTTP_422_UNPROCESSABLE_CONTENT)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={**_NOT_FOUND, **_CONFLICT, **_INVALID},
)
async def create_reservation(
    body: ReservationCreate, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> ReservationRead:
    """Book GPUs on a server. Reservations on servers that require approval start as
    `pending`; others are `approved` immediately. Rule violations that depend only on
    the request and the server's settings return 422; conflicts with other bookings,
    the quota, or the server's state return 409."""
    reservation = await reservations.create_reservation(
        session,
        user,
        reservations.NewReservation(**body.model_dump()),
        now=datetime.now(UTC),
        quota_zone=settings.quota_zone,
    )
    return ReservationRead.build(reservation)


@router.get("", responses=_INVALID)
async def list_reservations(
    user: CurrentUser,
    session: SessionDep,
    paging: PageParamsDep,
    status: ReservationStatus | None = None,
    server_id: uuid.UUID | None = None,
    user_id: Annotated[
        uuid.UUID | None,
        Query(description="Admins only. Students always see their own reservations."),
    ] = None,
    overlaps_from: Annotated[
        AwareDatetime | None,
        Query(
            alias="from",
            description="Only reservations that end after this time (ends_at > from).",
        ),
    ] = None,
    order: Annotated[
        reservations.ReservationOrder,
        Query(description="`-starts_at` (default) for newest first, `starts_at` for soonest."),
    ] = reservations.ReservationOrder.NEWEST_FIRST,
    overlaps_to: Annotated[
        AwareDatetime | None,
        Query(
            alias="to",
            description=(
                "Only reservations that start before this time (starts_at < to). With "
                "`from`, this selects reservations overlapping [from, to)."
            ),
        ),
    ] = None,
) -> Page[ReservationRead]:
    """Reservations by start time. Students see their own; admins see all."""
    listing = await reservations.list_reservations(
        session,
        user,
        reservations.ReservationFilters(
            status=status,
            server_id=server_id,
            user_id=user_id,
            overlaps_from=overlaps_from,
            overlaps_to=overlaps_to,
            order=order,
        ),
        offset=paging.offset,
        limit=paging.page_size,
    )
    return Page[ReservationRead](
        items=[ReservationRead.build(r) for r in listing.items],
        total=listing.total,
        page=paging.page,
        page_size=paging.page_size,
    )


@router.get("/{reservation_id}", responses=_NOT_FOUND)
async def get_reservation(
    reservation_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> ReservationRead:
    """The owner's or any reservation for admins; others get 404."""
    return ReservationRead.build(await reservations.get_reservation(session, user, reservation_id))


@router.post("/{reservation_id}/cancel", responses={**_NOT_FOUND, **_CONFLICT})
async def cancel_reservation(
    reservation_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> ReservationRead:
    """Cancel a pending reservation, or an approved one that has not started. A running
    reservation ends early instead: its end moves to the next half hour and it becomes
    `completed`. Owners and admins."""
    reservation = await reservations.cancel_reservation(
        session, user, reservation_id, now=datetime.now(UTC)
    )
    return ReservationRead.build(reservation)


@router.post("/{reservation_id}/approve", responses={**_ADMIN, **_NOT_FOUND, **_CONFLICT})
async def approve_reservation(
    reservation_id: uuid.UUID, admin: AdminUser, session: SessionDep
) -> ReservationRead:
    """Approve a pending reservation that has not started."""
    reservation = await reservations.approve_reservation(
        session, admin, reservation_id, now=datetime.now(UTC)
    )
    return ReservationRead.build(reservation)


@router.post(
    "/{reservation_id}/reject",
    responses={**_ADMIN, **_NOT_FOUND, **_CONFLICT, **_INVALID},
)
async def reject_reservation(
    reservation_id: uuid.UUID, body: ReservationReject, admin: AdminUser, session: SessionDep
) -> ReservationRead:
    """Reject a pending reservation with a reason, releasing its GPUs."""
    reservation = await reservations.reject_reservation(
        session, admin, reservation_id, reason=body.reason, now=datetime.now(UTC)
    )
    return ReservationRead.build(reservation)
