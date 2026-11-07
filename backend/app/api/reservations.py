from datetime import UTC, datetime

from fastapi import APIRouter, status

from app.api import problem_responses
from app.api.deps import CurrentUser, SessionDep
from app.schemas.reservation import ReservationCreate, ReservationRead
from app.services import reservations

router = APIRouter(
    prefix="/reservations",
    tags=["reservations"],
    responses=problem_responses(status.HTTP_401_UNAUTHORIZED),
)

_NOT_FOUND = problem_responses(status.HTTP_404_NOT_FOUND)
_CONFLICT = problem_responses(status.HTTP_409_CONFLICT)
_INVALID = problem_responses(status.HTTP_422_UNPROCESSABLE_CONTENT)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={**_NOT_FOUND, **_CONFLICT, **_INVALID},
)
async def create_reservation(
    body: ReservationCreate, user: CurrentUser, session: SessionDep
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
    )
    return ReservationRead.build(reservation)
