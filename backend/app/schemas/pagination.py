from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import Depends, Query
from pydantic import BaseModel

DEFAULT_PAGE_SIZE: Final = 20
MAX_PAGE_SIZE: Final = 100


class Page[ItemT](BaseModel):
    items: list[ItemT]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class PageParams:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def _page_params(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> PageParams:
    return PageParams(page=page, page_size=page_size)


PageParamsDep = Annotated[PageParams, Depends(_page_params)]
