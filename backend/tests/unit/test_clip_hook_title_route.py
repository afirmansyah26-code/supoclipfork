from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.api.routes import tasks
from src.editor_state import editor_key, set_store


async def test_saving_generated_hook_title_updates_the_editor_default(monkeypatch):
    task_id, clip_id, filename = "task", "clip", "clip.mp4"
    old_clip = {
        "id": clip_id, "task_id": task_id, "filename": filename,
        "hook_title": "Old generated title", "workflow_meta": {},
    }
    updated_clip = {**old_clip, "hook_title": "New generated title"}
    clip_repo = SimpleNamespace(
        get_clip_by_id=AsyncMock(side_effect=[old_clip, updated_clip]),
    )
    service = SimpleNamespace(clip_repo=clip_repo)
    monkeypatch.setattr(tasks, "TaskService", lambda _db: service)
    monkeypatch.setattr(tasks, "_require_task_owner", AsyncMock(return_value={"id": task_id}))
    monkeypatch.setattr(tasks, "_read_json_object", AsyncMock(return_value={
        "hook_title": "New generated title",
        "title": "Post title",
        "description": "Post description",
    }))
    db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())

    class EditorStoreStub:
        def __init__(self):
            self.calls = []

        async def sync_clip_hook_title(self, *args):
            self.calls.append(args)
            return True

    store = EditorStoreStub()
    set_store(store)
    key = editor_key(task_id, clip_id, filename)

    result = await tasks.update_clip_hook_title(task_id, clip_id, None, db)

    assert result["clip"]["hook_title"] == "New generated title"
    assert result["title"] == "Post title"
    assert store.calls == [(key, "Old generated title", "New generated title")]
    set_store(None)
