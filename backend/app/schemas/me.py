from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from app.schemas.auth import PASSWORD_MAX_LENGTH, FullName, NewPassword
from app.schemas.upload import ObjectKey


class UpdateProfileRequest(BaseModel):
    # Unknown fields are rejected, so a client cannot try to set its own role or quota.
    model_config = ConfigDict(extra="forbid")

    full_name: FullName | None = None
    # A key from POST /uploads/presign (purpose `avatar`) whose upload has finished.
    # null removes the avatar; omitted leaves it unchanged.
    avatar_key: ObjectKey | None = None


class ChangePasswordRequest(BaseModel):
    current_password: Annotated[str, Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)]
    new_password: NewPassword

    @field_validator("new_password")
    @classmethod
    def differs_from_current(cls, value: str, info: ValidationInfo) -> str:
        # Reported on new_password so a form can show it under that field.
        if value == info.data.get("current_password"):
            raise ValueError("must differ from the current password")
        return value
