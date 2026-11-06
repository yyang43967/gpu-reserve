"""A marker for "field not sent" in partial updates, where None means "clear it"."""

from enum import Enum
from typing import Final, Literal


class _Unset(Enum):
    UNSET = "UNSET"


UNSET: Final = _Unset.UNSET
Unset = Literal[_Unset.UNSET]
