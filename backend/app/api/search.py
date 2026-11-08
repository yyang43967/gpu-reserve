from datetime import UTC, datetime

from fastapi import APIRouter, status

from app.api import problem_responses
from app.api.deps import CurrentUser, SessionDep, SettingsDep
from app.schemas.search import SlotRead, SlotSearchRequest, SlotSearchResponse
from app.services import slot_search

router = APIRouter(
    prefix="/search",
    tags=["search"],
    responses=problem_responses(status.HTTP_401_UNAUTHORIZED),
)


@router.post(
    "/slots",
    responses=problem_responses(status.HTTP_409_CONFLICT, status.HTTP_422_UNPROCESSABLE_CONTENT),
)
async def search_slots(
    body: SlotSearchRequest, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> SlotSearchResponse:
    """Find (server, start, end) candidates for a job, ranked. Only servers open for
    booking are searched, and every booking rule is applied, including the caller's
    quota. Booking a result can still fail if someone else books it first. 409 when
    the caller already holds the most reservations allowed."""
    found = await slot_search.search_slots(
        session,
        user,
        body.to_query(),
        now=datetime.now(UTC),
        quota_zone=settings.quota_zone,
    )
    return SlotSearchResponse(items=[SlotRead.build(candidate) for candidate in found])
