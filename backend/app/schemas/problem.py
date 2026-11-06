from pydantic import BaseModel


class FieldError(BaseModel):
    field: str
    message: str


class Problem(BaseModel):
    """An RFC 7807 problem details document."""

    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None


class ValidationProblem(Problem):
    errors: list[FieldError]
