from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.user import OBJECT_KEY_MAX_LENGTH
from app.services.uploads import UploadPurpose

# A key returned by POST /uploads/presign.
ObjectKey = Annotated[str, StringConstraints(min_length=1, max_length=OBJECT_KEY_MAX_LENGTH)]


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
