from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.services.uploads import UploadPurpose


class PresignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: UploadPurpose
    content_type: Annotated[str, Field(min_length=1, max_length=100)]
    size_bytes: Annotated[int, Field(ge=1)]


class PresignResponse(BaseModel):
    """Send a multipart/form-data POST to `url` with every entry of `fields`, then the
    file as the last field, named `file`."""

    key: str
    url: str
    fields: dict[str, str]
    max_bytes: int
    expires_at: datetime
