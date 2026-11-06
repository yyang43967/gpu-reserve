import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api import problem_responses
from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.core.unset import UNSET
from app.models import GpuModel, ServerStatus
from app.models.server import MAX_GPUS_PER_SERVER
from app.schemas.pagination import Page, PageParamsDep
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


@router.get("")
async def list_servers(
    user: CurrentUser,
    session: SessionDep,
    paging: PageParamsDep,
    lab_id: uuid.UUID | None = None,
    gpu_model: GpuModel | None = None,
    min_gpu_memory_gb: Annotated[int | None, Query(ge=1)] = None,
    min_gpus: Annotated[int | None, Query(ge=1, le=MAX_GPUS_PER_SERVER)] = None,
    status: ServerStatus | None = None,
) -> Page[ServerRead]:
    """Servers ordered by lab and hostname. `min_gpu_memory_gb` is per GPU; `min_gpus`
    compares with the server's GPU count. Students do not see retired servers or
    servers in inactive labs."""
    filters = servers.ServerFilters(
        lab_id=lab_id,
        gpu_model=gpu_model,
        min_gpu_memory_gb=min_gpu_memory_gb,
        min_gpus=min_gpus,
        status=status,
    )
    listing = await servers.list_servers(
        session, user, filters, offset=paging.offset, limit=paging.page_size
    )
    return Page[ServerRead](
        items=[ServerRead.model_validate(server) for server in listing.items],
        total=listing.total,
        page=paging.page,
        page_size=paging.page_size,
    )


@router.get("/{server_id}", responses=_NOT_FOUND)
async def get_server(server_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> ServerRead:
    return ServerRead.model_validate(await servers.get_server(session, user, server_id))


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
