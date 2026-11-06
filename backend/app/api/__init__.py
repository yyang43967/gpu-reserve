from typing import Any, Final

from app.schemas.problem import Problem

API_PREFIX: Final = "/api/v1"


def problem_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI entries documenting problem-details error responses."""
    return {code: {"model": Problem} for code in status_codes}
