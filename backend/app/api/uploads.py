from datetime import UTC, datetime

from fastapi import APIRouter, status

from app.api import problem_responses
from app.api.deps import ChangeableUser, RateLimiterDep, SettingsDep, StorageDep
from app.schemas.upload import PresignRequest, PresignResponse
from app.services import uploads

router = APIRouter(
    prefix="/uploads",
    tags=["uploads"],
    responses=problem_responses(status.HTTP_401_UNAUTHORIZED),
)


@router.post(
    "/presign",
    responses=problem_responses(
        status.HTTP_403_FORBIDDEN,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        status.HTTP_429_TOO_MANY_REQUESTS,
    ),
)
async def presign_upload(
    body: PresignRequest,
    user: ChangeableUser,
    storage: StorageDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
) -> PresignResponse:
    """Get a short-lived form to upload one PNG, JPEG, or WebP image straight to storage.

    Avatars (up to 2 MiB) are open to every signed-in user; server images (up to 5 MiB)
    to admins only. Save the returned key on the profile or server afterwards. Each
    user may request a limited number of upload forms per window (429 beyond it). The
    shared demo account cannot upload (403): its avatar would show to every visitor.
    """
    await limiter.check_presign(user.id)
    ticket = uploads.create_upload(
        storage,
        user,
        purpose=body.purpose,
        content_type=body.content_type,
        size_bytes=body.size_bytes,
        expires_in=settings.upload_url_ttl_seconds,
        now=datetime.now(UTC),
    )
    return PresignResponse(
        key=ticket.key,
        url=ticket.url,
        fields=ticket.fields,
        max_bytes=ticket.max_bytes,
        expires_at=ticket.expires_at,
    )
