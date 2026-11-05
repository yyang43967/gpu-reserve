from fastapi import APIRouter, Request, Response, status

from app.schemas.health import HealthResponse, ReadinessResponse, ReadinessStatus
from app.services.health import check_readiness

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> HealthResponse:
    """Liveness: the process is up. Does no I/O."""
    return HealthResponse()


@router.get(
    "/ready",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def ready(request: Request, response: Response) -> ReadinessResponse:
    """Readiness: the database and Redis are reachable."""
    state = request.app.state
    result = await check_readiness(
        state.engine, state.redis, state.settings.ready_check_timeout_seconds
    )
    if result.status is ReadinessStatus.NOT_READY:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result
