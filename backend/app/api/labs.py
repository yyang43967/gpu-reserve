import uuid

from fastapi import APIRouter, Response, status

from app.api import problem_responses
from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.core.unset import UNSET
from app.schemas.lab import LabCreate, LabRead, LabUpdate
from app.schemas.pagination import Page, PageParamsDep
from app.services import labs

router = APIRouter(
    prefix="/labs", tags=["labs"], responses=problem_responses(status.HTTP_401_UNAUTHORIZED)
)

_NOT_FOUND = problem_responses(status.HTTP_404_NOT_FOUND)
_ADMIN = problem_responses(status.HTTP_403_FORBIDDEN)
_CONFLICT = problem_responses(status.HTTP_409_CONFLICT)


@router.get("")
async def list_labs(
    user: CurrentUser,
    session: SessionDep,
    paging: PageParamsDep,
    is_active: bool | None = None,
) -> Page[LabRead]:
    """Labs ordered by name. Students only see active labs."""
    listing = await labs.list_labs(
        session, user, offset=paging.offset, limit=paging.page_size, is_active=is_active
    )
    return Page[LabRead](
        items=[LabRead.model_validate(lab) for lab in listing.items],
        total=listing.total,
        page=paging.page,
        page_size=paging.page_size,
    )


@router.post("", status_code=status.HTTP_201_CREATED, responses={**_ADMIN, **_CONFLICT})
async def create_lab(body: LabCreate, admin: AdminUser, session: SessionDep) -> LabRead:
    lab = await labs.create_lab(
        session,
        name=body.name,
        location=body.location,
        timezone=body.timezone,
        description=body.description,
        is_active=body.is_active,
    )
    return LabRead.model_validate(lab)


@router.get("/{lab_id}", responses=_NOT_FOUND)
async def get_lab(lab_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> LabRead:
    return LabRead.model_validate(await labs.get_lab(session, user, lab_id))


@router.patch("/{lab_id}", responses={**_ADMIN, **_NOT_FOUND, **_CONFLICT})
async def update_lab(
    lab_id: uuid.UUID, body: LabUpdate, admin: AdminUser, session: SessionDep
) -> LabRead:
    """Change any field. Omitted fields stay as they are; `description: null` clears it."""
    lab = await labs.update_lab(
        session,
        admin,
        lab_id,
        name=body.name,
        location=body.location,
        timezone=body.timezone,
        description=body.description if "description" in body.model_fields_set else UNSET,
        is_active=body.is_active,
    )
    return LabRead.model_validate(lab)


@router.delete(
    "/{lab_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**_ADMIN, **_NOT_FOUND, **_CONFLICT},
)
async def delete_lab(lab_id: uuid.UUID, admin: AdminUser, session: SessionDep) -> Response:
    await labs.delete_lab(session, admin, lab_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
