import re
from typing import Any, Final

_REF: Final = re.compile(r"\[(\d{1,3})\]")


def cited_sources(text: str, sources: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The sources an answer cites with [n], in order of first citation. Numbers that
    match no retrieved section are ignored."""
    by_ref = {int(source["ref"]): source for source in sources.values()}
    cited: list[dict[str, Any]] = []
    seen: set[int] = set()
    for match in _REF.finditer(text):
        ref = int(match.group(1))
        if ref in by_ref and ref not in seen:
            seen.add(ref)
            source = by_ref[ref]
            cited.append(
                {
                    "ref": ref,
                    "document": source["document"],
                    "title": source["title"],
                    "heading": source["heading"],
                }
            )
    return cited
