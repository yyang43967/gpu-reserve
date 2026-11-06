from fastapi import BackgroundTasks

from app.services import uploads
from app.services.storage import ObjectStorage


def delete_after_commit(
    background: BackgroundTasks, storage: ObjectStorage, key: str | None
) -> None:
    """Delete an object once the request's change is saved.

    Background tasks run after the response, and the request's transaction commits
    before the response is sent. A request that fails, including at commit, returns an
    error response without this task, so the object is kept.
    """
    if key is not None:
        background.add_task(uploads.delete_quietly, storage, key)
