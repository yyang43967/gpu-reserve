from enum import StrEnum

from pydantic import BaseModel


class HealthStatus(StrEnum):
    OK = "ok"


class ReadinessStatus(StrEnum):
    READY = "ready"
    NOT_READY = "not_ready"


class CheckResult(StrEnum):
    OK = "ok"
    FAIL = "fail"


class HealthResponse(BaseModel):
    status: HealthStatus = HealthStatus.OK


class ReadinessChecks(BaseModel):
    database: CheckResult
    redis: CheckResult


class ReadinessResponse(BaseModel):
    status: ReadinessStatus
    checks: ReadinessChecks
