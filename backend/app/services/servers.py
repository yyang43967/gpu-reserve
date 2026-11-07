import uuid
from dataclasses import dataclass

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, selectinload

from app.core.exceptions import HostnameTaken, ServerNotFound, UnknownLab
from app.core.unset import UNSET, Unset
from app.models import Gpu, GpuModel, Lab, Server, ServerStatus, User
from app.models.server import DEFAULT_MAX_BOOKING_HOURS, GPU_MEMORY_GB
from app.services.labs import can_see_inactive
from app.services.storage import ObjectStorage
from app.services.uploads import UploadPurpose, verify_upload

HOSTNAME_UNIQUE_CONSTRAINT = "uq_servers_hostname"


async def _ensure_lab_exists(session: AsyncSession, lab_id: uuid.UUID) -> None:
    if await session.get(Lab, lab_id) is None:
        raise UnknownLab(f"No lab with id {lab_id}.")


async def _ensure_hostname_free(
    session: AsyncSession, hostname: str, exclude_id: uuid.UUID | None = None
) -> None:
    query = select(Server.id).where(Server.hostname == hostname)
    if exclude_id is not None:
        query = query.where(Server.id != exclude_id)
    if await session.scalar(query) is not None:
        raise HostnameTaken(f"A server named {hostname!r} already exists.")


async def _flush_checking_hostname(session: AsyncSession, hostname: str) -> None:
    try:
        await session.flush()
    except IntegrityError as exc:
        # A concurrent request took the hostname after the check; the constraint decides.
        if HOSTNAME_UNIQUE_CONSTRAINT in str(exc.orig):
            raise HostnameTaken(f"A server named {hostname!r} already exists.") from exc
        raise


async def _lock_server(session: AsyncSession, server_id: uuid.UUID) -> Server:
    """Load a server with a row lock held until the transaction ends.

    Every change to a server's GPU rows happens under this lock, so two concurrent
    changes of gpu_count are applied one after the other, each seeing the other's result.
    """
    server = await session.scalar(
        select(Server)
        .where(Server.id == server_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if server is None:
        raise ServerNotFound(f"No server with id {server_id}.")
    return server


async def get_server_with_lab(session: AsyncSession, server_id: uuid.UUID) -> Server:
    server = await session.scalar(
        select(Server)
        .where(Server.id == server_id)
        .options(selectinload(Server.lab))
        .execution_options(populate_existing=True)
    )
    if server is None:
        raise ServerNotFound(f"No server with id {server_id}.")
    return server


# --- browsing ---


@dataclass(frozen=True)
class ServerFilters:
    lab_id: uuid.UUID | None = None
    gpu_model: GpuModel | None = None
    min_gpu_memory_gb: int | None = None
    min_gpus: int | None = None
    status: ServerStatus | None = None


@dataclass(frozen=True)
class ServerListing:
    items: list[Server]
    total: int


def visibility_conditions(viewer: User) -> list[ColumnElement[bool]]:
    """Students see servers that are not retired, in active labs. Admins see all."""
    if can_see_inactive(viewer):
        return []
    return [Lab.is_active.is_(True), Server.status != ServerStatus.RETIRED]


async def list_servers(
    session: AsyncSession,
    viewer: User,
    filters: ServerFilters,
    *,
    offset: int,
    limit: int,
) -> ServerListing:
    """Servers the viewer may see, ordered by lab name and hostname."""
    conditions = visibility_conditions(viewer)
    if filters.lab_id is not None:
        conditions.append(Server.lab_id == filters.lab_id)
    if filters.gpu_model is not None:
        conditions.append(Server.gpu_model == filters.gpu_model)
    if filters.min_gpu_memory_gb is not None:
        conditions.append(Server.gpu_memory_gb >= filters.min_gpu_memory_gb)
    if filters.min_gpus is not None:
        conditions.append(Server.gpu_count >= filters.min_gpus)
    if filters.status is not None:
        conditions.append(Server.status == filters.status)

    base = select(Server).join(Server.lab).where(*conditions)
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = await session.scalars(
        base.options(contains_eager(Server.lab))
        .order_by(func.lower(Lab.name), Server.hostname)
        .offset(offset)
        .limit(limit)
    )
    return ServerListing(items=list(rows), total=total or 0)


async def get_server(session: AsyncSession, viewer: User, server_id: uuid.UUID) -> Server:
    """A server the viewer may see. Hidden servers look missing to students."""
    server = await session.scalar(
        select(Server)
        .join(Server.lab)
        .where(Server.id == server_id, *visibility_conditions(viewer))
        .options(contains_eager(Server.lab))
    )
    if server is None:
        raise ServerNotFound(f"No server with id {server_id}.")
    return server


# --- GPU rows ---


async def _ensure_gpus_removable(
    session: AsyncSession, server_id: uuid.UUID, min_index: int
) -> None:
    """Raise GpusInUse if GPUs numbered `min_index` and up have future active allocations.

    Allocations do not exist yet, so nothing can block a reduction. This is the one place
    the check will go, so every path that removes GPUs already calls it.
    """


async def _ensure_server_deletable(session: AsyncSession, server_id: uuid.UUID) -> None:
    """Raise GpusInUse if the server has future active allocations.

    Like `_ensure_gpus_removable`, a no-op until allocations exist.
    """


async def _sync_gpus(session: AsyncSession, server: Server, old_count: int) -> None:
    """Make the active GPU rows of `server` match `server.gpu_count`.

    GPUs are numbered 0..gpu_count-1. Lowering the count soft-removes the highest
    numbers. Raising it reactivates removed rows for the missing numbers before
    inserting new ones, so a GPU that comes back keeps its row and its history.
    The caller holds the server's row lock.
    """
    new_count = server.gpu_count
    if new_count < old_count:
        await _ensure_gpus_removable(session, server.id, min_index=new_count)
        await session.execute(
            update(Gpu)
            .where(Gpu.server_id == server.id, Gpu.removed_at.is_(None), Gpu.index >= new_count)
            .values(removed_at=func.now())
        )
    elif new_count > old_count:
        wanted = range(old_count, new_count)
        # The most recently removed row for each missing number.
        removed = await session.scalars(
            select(Gpu)
            .where(Gpu.server_id == server.id, Gpu.removed_at.is_not(None), Gpu.index.in_(wanted))
            .order_by(Gpu.index, Gpu.removed_at.desc())
            .ext(distinct_on(Gpu.index))
        )
        reactivated = set()
        for gpu in removed:
            gpu.removed_at = None
            reactivated.add(gpu.index)
        session.add_all(
            Gpu(server_id=server.id, index=index) for index in wanted if index not in reactivated
        )
    await session.flush()


async def active_gpu_indexes(session: AsyncSession, server_id: uuid.UUID) -> list[int]:
    rows = await session.scalars(
        select(Gpu.index)
        .where(Gpu.server_id == server_id, Gpu.removed_at.is_(None))
        .order_by(Gpu.index)
    )
    return list(rows)


# --- administration ---


async def create_server(
    session: AsyncSession,
    *,
    lab_id: uuid.UUID,
    hostname: str,
    gpu_model: GpuModel,
    gpu_count: int,
    cpu_cores: int,
    ram_gb: int,
    interconnect: str | None = None,
    status: ServerStatus = ServerStatus.AVAILABLE,
    requires_approval: bool = False,
    max_booking_hours: int = DEFAULT_MAX_BOOKING_HOURS,
    notes: str | None = None,
) -> Server:
    """Create a server and its GPU rows. GPU memory follows from the model."""
    await _ensure_lab_exists(session, lab_id)
    await _ensure_hostname_free(session, hostname)
    server = Server(
        lab_id=lab_id,
        hostname=hostname,
        gpu_model=gpu_model,
        gpu_memory_gb=GPU_MEMORY_GB[gpu_model],
        gpu_count=gpu_count,
        cpu_cores=cpu_cores,
        ram_gb=ram_gb,
        interconnect=interconnect or None,
        status=status,
        requires_approval=requires_approval,
        max_booking_hours=max_booking_hours,
        notes=notes or None,
    )
    session.add(server)
    await _flush_checking_hostname(session, hostname)
    await _sync_gpus(session, server, old_count=0)
    return await get_server_with_lab(session, server.id)


async def update_server(
    session: AsyncSession,
    server_id: uuid.UUID,
    *,
    lab_id: uuid.UUID | None = None,
    hostname: str | None = None,
    gpu_model: GpuModel | None = None,
    gpu_count: int | None = None,
    cpu_cores: int | None = None,
    ram_gb: int | None = None,
    interconnect: str | Unset | None = UNSET,
    status: ServerStatus | None = None,
    requires_approval: bool | None = None,
    max_booking_hours: int | None = None,
    notes: str | Unset | None = UNSET,
) -> Server:
    """Change any of the given fields. Pass None for interconnect or notes to clear them."""
    server = await _lock_server(session, server_id)
    old_count = server.gpu_count

    if lab_id is not None and lab_id != server.lab_id:
        await _ensure_lab_exists(session, lab_id)
        server.lab_id = lab_id
    if hostname is not None and hostname != server.hostname:
        await _ensure_hostname_free(session, hostname, exclude_id=server.id)
        server.hostname = hostname
    if gpu_model is not None:
        server.gpu_model = gpu_model
        server.gpu_memory_gb = GPU_MEMORY_GB[gpu_model]
    if gpu_count is not None:
        server.gpu_count = gpu_count
    if cpu_cores is not None:
        server.cpu_cores = cpu_cores
    if ram_gb is not None:
        server.ram_gb = ram_gb
    if interconnect is not UNSET:
        server.interconnect = interconnect or None
    if status is not None:
        server.status = status
    if requires_approval is not None:
        server.requires_approval = requires_approval
    if max_booking_hours is not None:
        server.max_booking_hours = max_booking_hours
    if notes is not UNSET:
        server.notes = notes or None

    await _flush_checking_hostname(session, server.hostname)
    if server.gpu_count != old_count:
        await _sync_gpus(session, server, old_count=old_count)
    return await get_server_with_lab(session, server.id)


async def set_image(
    session: AsyncSession,
    storage: ObjectStorage,
    actor: User,
    server_id: uuid.UUID,
    image_key: str | None,
) -> str | None:
    """Point the server at an uploaded image, or remove it with None.

    The upload is verified before the key is saved. Returns the key this replaced, which
    the caller deletes from storage once the transaction has committed, or None when
    nothing was replaced (including when the key did not change).
    """
    server = await _lock_server(session, server_id)
    if image_key == server.image_key:
        return None
    if image_key is not None:
        await verify_upload(storage, image_key, purpose=UploadPurpose.SERVER_IMAGE, owner=actor)
    replaced = server.image_key
    server.image_key = image_key
    await session.flush()
    return replaced


async def delete_server(session: AsyncSession, server_id: uuid.UUID) -> str | None:
    """Delete a server. Its GPU rows go with it (ON DELETE CASCADE).

    Returns the server's image key, which the caller deletes from storage once the
    transaction has committed.
    """
    server = await _lock_server(session, server_id)
    await _ensure_server_deletable(session, server.id)
    image_key = server.image_key
    await session.delete(server)
    await session.flush()
    return image_key
