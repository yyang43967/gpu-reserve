"""Who may call each route: anonymous, student, and admin, for every API route.

`test_every_route_is_in_the_matrix` fails when a route is added without an entry
here, so new routes cannot skip a permission decision.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.main import create_app
from app.models import GpuModel, Lab, Server, User, UserRole
from app.services import labs, servers, users

pytestmark = pytest.mark.integration

PREFIX = "/api/v1"
PASSWORD = "correct-horse-battery"


class Access(StrEnum):
    PUBLIC = "public"
    # Authenticated by the refresh cookie, not an access token.
    REFRESH_COOKIE = "refresh_cookie"
    AUTHENTICATED = "authenticated"
    ADMIN = "admin"


@dataclass(frozen=True)
class Targets:
    """Records that routes with path parameters act on."""

    user: User
    lab: Lab
    # A lab with no servers, so deleting it succeeds.
    empty_lab: Lab
    server: Server


@dataclass(frozen=True)
class Context:
    caller: User
    target: User
    lab: Lab
    empty_lab: Lab
    server: Server

    @classmethod
    def of(cls, caller: User, targets: Targets) -> "Context":
        return cls(
            caller=caller,
            target=targets.user,
            lab=targets.lab,
            empty_lab=targets.empty_lab,
            server=targets.server,
        )


@dataclass(frozen=True)
class RouteCase:
    method: str
    path: str
    access: Access
    # Builds a valid request body for a successful call.
    body: Callable[[Context], dict[str, Any]] | None = None
    path_params: Callable[[Context], dict[str, str]] = field(default=lambda ctx: {})

    @property
    def id(self) -> str:
        return f"{self.method} {self.path}"

    def url(self, ctx: Context) -> str:
        return PREFIX + self.path.format(**self.path_params(ctx))


def _target(ctx: Context) -> dict[str, str]:
    return {"user_id": str(ctx.target.id)}


def _lab(ctx: Context) -> dict[str, str]:
    return {"lab_id": str(ctx.lab.id)}


def _empty_lab(ctx: Context) -> dict[str, str]:
    return {"lab_id": str(ctx.empty_lab.id)}


def _server(ctx: Context) -> dict[str, str]:
    return {"server_id": str(ctx.server.id)}


MATRIX = [
    RouteCase("GET", "/health", Access.PUBLIC),
    RouteCase("GET", "/ready", Access.PUBLIC),
    RouteCase(
        "POST",
        "/auth/register",
        Access.PUBLIC,
        body=lambda ctx: {"email": "new@example.edu", "password": PASSWORD, "full_name": "New"},
    ),
    RouteCase(
        "POST",
        "/auth/login",
        Access.PUBLIC,
        body=lambda ctx: {"email": ctx.target.email, "password": PASSWORD},
    ),
    RouteCase("POST", "/auth/refresh", Access.REFRESH_COOKIE),
    RouteCase("POST", "/auth/logout", Access.AUTHENTICATED),
    RouteCase("GET", "/me", Access.AUTHENTICATED),
    RouteCase("PATCH", "/me", Access.AUTHENTICATED, body=lambda ctx: {"full_name": "Renamed"}),
    RouteCase(
        "PUT",
        "/me/password",
        Access.AUTHENTICATED,
        body=lambda ctx: {"current_password": PASSWORD, "new_password": "another-password"},
    ),
    RouteCase("GET", "/users", Access.ADMIN),
    RouteCase(
        "POST",
        "/users",
        Access.ADMIN,
        body=lambda ctx: {"email": "made@example.edu", "full_name": "Made", "password": PASSWORD},
    ),
    RouteCase("GET", "/users/{user_id}", Access.ADMIN, path_params=_target),
    RouteCase(
        "PATCH",
        "/users/{user_id}",
        Access.ADMIN,
        body=lambda ctx: {"weekly_gpu_hour_quota": 10},
        path_params=_target,
    ),
    RouteCase("DELETE", "/users/{user_id}", Access.ADMIN, path_params=_target),
    RouteCase("GET", "/labs", Access.AUTHENTICATED),
    RouteCase(
        "POST",
        "/labs",
        Access.ADMIN,
        body=lambda ctx: {"name": "Robotics Lab", "location": "Building 7"},
    ),
    RouteCase("GET", "/labs/{lab_id}", Access.AUTHENTICATED, path_params=_lab),
    RouteCase(
        "PATCH",
        "/labs/{lab_id}",
        Access.ADMIN,
        body=lambda ctx: {"location": "Building 9"},
        path_params=_lab,
    ),
    RouteCase("DELETE", "/labs/{lab_id}", Access.ADMIN, path_params=_empty_lab),
    RouteCase("GET", "/servers", Access.AUTHENTICATED),
    RouteCase("GET", "/servers/{server_id}", Access.AUTHENTICATED, path_params=_server),
    RouteCase(
        "POST",
        "/servers",
        Access.ADMIN,
        body=lambda ctx: {
            "lab_id": str(ctx.lab.id),
            "hostname": "h100-node-09",
            "gpu_model": "H100_80GB",
            "gpu_count": 8,
            "cpu_cores": 96,
            "ram_gb": 1024,
        },
    ),
    RouteCase(
        "PATCH",
        "/servers/{server_id}",
        Access.ADMIN,
        body=lambda ctx: {"gpu_count": 2},
        path_params=_server,
    ),
    RouteCase("DELETE", "/servers/{server_id}", Access.ADMIN, path_params=_server),
    # Open to every signed-in user for avatars. Server images are admin-only; that rule
    # lives in the uploads service and is tested in test_uploads_api.
    RouteCase(
        "POST",
        "/uploads/presign",
        Access.AUTHENTICATED,
        body=lambda ctx: {"purpose": "avatar", "content_type": "image/png", "size_bytes": 1000},
    ),
]

PROTECTED = [case for case in MATRIX if case.access in {Access.AUTHENTICATED, Access.ADMIN}]
ADMIN_ONLY = [case for case in MATRIX if case.access is Access.ADMIN]
CALLABLE_BY_STUDENT = [
    case for case in MATRIX if case.access in {Access.PUBLIC, Access.AUTHENTICATED}
]


def _ids(cases: list[RouteCase]) -> list[str]:
    return [case.id for case in cases]


def test_every_route_is_in_the_matrix() -> None:
    """Compares against the OpenAPI document, the public list of the app's routes.

    A route registered with include_in_schema=False would not appear there; none are.
    """
    paths = create_app(Settings()).openapi()["paths"]
    routes = {
        f"{method.upper()} {path.removeprefix(PREFIX)}"
        for path, operations in paths.items()
        for method in operations
    }

    assert routes == {case.id for case in MATRIX}


# --- fixtures ---


async def _create(db: AsyncSession, email: str, role: UserRole) -> User:
    user = await users.create_user(
        db, email=email, password=PASSWORD, full_name=email.split("@")[0], role=role
    )
    await db.commit()
    return user


@dataclass(frozen=True)
class Caller:
    user: User
    headers: dict[str, str]


async def _caller(client: AsyncClient, db: AsyncSession, email: str, role: UserRole) -> Caller:
    user = await _create(db, email, role)
    response = await client.post(
        f"{PREFIX}/auth/login", json={"email": email, "password": PASSWORD}
    )
    token = response.json()["access_token"]
    return Caller(user=user, headers={"Authorization": f"Bearer {token}"})


@pytest.fixture
async def admin(api_client: AsyncClient, db_session: AsyncSession) -> Caller:
    return await _caller(api_client, db_session, "admin@example.edu", UserRole.ADMIN)


@pytest.fixture
async def student(api_client: AsyncClient, db_session: AsyncSession) -> Caller:
    return await _caller(api_client, db_session, "student@example.edu", UserRole.STUDENT)


async def _lab_named(db: AsyncSession, name: str) -> Lab:
    lab = await labs.create_lab(
        db,
        name=name,
        location="Building 3",
        timezone="America/Los_Angeles",
        description=None,
        is_active=True,
    )
    await db.commit()
    return lab


@pytest.fixture
async def targets(db_session: AsyncSession) -> Targets:
    lab = await _lab_named(db_session, "Vision Lab")
    server = await servers.create_server(
        db_session,
        lab_id=lab.id,
        hostname="a100-node-01",
        gpu_model=GpuModel.A100_80GB,
        gpu_count=4,
        cpu_cores=64,
        ram_gb=512,
    )
    await db_session.commit()
    return Targets(
        # Another account that user-management routes act on.
        user=await _create(db_session, "target@example.edu", UserRole.STUDENT),
        lab=lab,
        empty_lab=await _lab_named(db_session, "Empty Lab"),
        server=server,
    )


async def call(
    client: AsyncClient, case: RouteCase, ctx: Context, headers: dict[str, str] | None = None
) -> Response:
    client.cookies.clear()  # only the explicit headers count
    body = case.body(ctx) if case.body else None
    return await client.request(case.method, case.url(ctx), json=body, headers=headers or {})


def assert_problem(response: Response, status: int, slug: str) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"].endswith(f"/{slug}")


def assert_allowed(response: Response) -> None:
    assert 200 <= response.status_code < 300, response.text


# --- anonymous ---


@pytest.mark.parametrize("case", PROTECTED, ids=_ids(PROTECTED))
async def test_anonymous_is_unauthenticated(
    api_client: AsyncClient, admin: Caller, targets: Targets, case: RouteCase
) -> None:
    response = await call(api_client, case, Context.of(admin.user, targets))

    assert_problem(response, 401, "not-authenticated")
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("case", PROTECTED, ids=_ids(PROTECTED))
async def test_bad_token_is_rejected(
    api_client: AsyncClient, admin: Caller, targets: Targets, case: RouteCase
) -> None:
    ctx = Context.of(admin.user, targets)

    response = await call(api_client, case, ctx, {"Authorization": "Bearer not-a-token"})

    assert_problem(response, 401, "invalid-token")


async def test_refresh_ignores_access_tokens(
    api_client: AsyncClient, student: Caller, targets: Targets
) -> None:
    [case] = [c for c in MATRIX if c.access is Access.REFRESH_COOKIE]

    response = await call(api_client, case, Context.of(student.user, targets), student.headers)

    assert_problem(response, 401, "invalid-token")


# --- student ---


@pytest.mark.parametrize("case", ADMIN_ONLY, ids=_ids(ADMIN_ONLY))
async def test_student_is_forbidden_on_admin_routes(
    api_client: AsyncClient, student: Caller, targets: Targets, case: RouteCase
) -> None:
    response = await call(api_client, case, Context.of(student.user, targets), student.headers)

    assert_problem(response, 403, "forbidden")


@pytest.mark.parametrize("case", CALLABLE_BY_STUDENT, ids=_ids(CALLABLE_BY_STUDENT))
async def test_student_is_allowed(
    api_client: AsyncClient, student: Caller, targets: Targets, case: RouteCase
) -> None:
    response = await call(api_client, case, Context.of(student.user, targets), student.headers)

    assert_allowed(response)


# --- admin ---


ADMIN_CALLABLE = [case for case in MATRIX if case.access is not Access.REFRESH_COOKIE]


@pytest.mark.parametrize("case", ADMIN_CALLABLE, ids=_ids(ADMIN_CALLABLE))
async def test_admin_is_allowed(
    api_client: AsyncClient, admin: Caller, targets: Targets, case: RouteCase
) -> None:
    response = await call(api_client, case, Context.of(admin.user, targets), admin.headers)

    assert_allowed(response)
