import uuid

from fastapi import APIRouter, Response, status

from app.api import problem_responses
from app.api.deps import AdminUser, SessionDep
from app.core.unset import UNSET
from app.schemas.server import ServerCreate, ServerRead, ServerUpdate
from app.services import servers

router = APIRouter(
    prefix="/servers",
    tags=["servers"],
    responses=problem_responses(status.HTTP_401_UNAUTHORIZED),
)

_ADMIN = problem_responses(status.HTTP_403_FORBIDDEN)
_NOT_FOUND = problem_responses(status.HTTP_404_NOT_FOUND)
_CONFLICT = problem_responses(status.HTTP_409_CONFLICT)
_INVALID = problem_responses(status.HTTP_422_UNPROCESSABLE_CONTENT)


@router.post("", status_code=status.HTTP_201_CREATED, responses={**_ADMIN, **_CONFLICT, **_INVALID})
async def create_server(body: ServerCreate, admin: AdminUser, session: SessionDep) -> ServerRead:
    """Create a server and its GPUs. GPU memory follows from the GPU model."""
    server = await servers.create_server(session, **body.model_dump())
    return ServerRead.model_validate(server)


@router.patch("/{server_id}", responses={**_ADMIN, **_NOT_FOUND, **_CONFLICT, **_INVALID})
async def update_server(
    server_id: uuid.UUID, body: ServerUpdate, admin: AdminUser, session: SessionDep
) -> ServerRead:
    """Change any field. Omitted fields stay as they are; `interconnect: null` and
    `notes: null` clear them. Lowering `gpu_count` removes the highest-numbered GPUs."""
    sent = body.model_fields_set
    server = await servers.update_server(
        session,
        server_id,
        lab_id=body.lab_id,
        hostname=body.hostname,
        gpu_model=body.gpu_model,
        gpu_count=body.gpu_count,
        cpu_cores=body.cpu_cores,
        ram_gb=body.ram_gb,
        interconnect=body.interconnect if "interconnect" in sent else UNSET,
        status=body.status,
        requires_approval=body.requires_approval,
        max_booking_hours=body.max_booking_hours,
        notes=body.notes if "notes" in sent else UNSET,
    )
    return ServerRead.model_validate(server)


@router.delete(
    "/{server_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**_ADMIN, **_NOT_FOUND, **_CONFLICT},
)
async def delete_server(server_id: uuid.UUID, admin: AdminUser, session: SessionDep) -> Response:
    await servers.delete_server(session, server_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
