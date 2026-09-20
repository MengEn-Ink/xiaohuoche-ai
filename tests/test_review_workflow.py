from __future__ import annotations

import json

from xzq.llm import LLMClient
from xzq.models import Material
from xzq.reviewer.watch import scan_pending_materials


def test_watch_only_writes_pending_manifest(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"; inbox.mkdir()
    (inbox / "new.jpg").write_bytes(b"new")
    destination = tmp_path / "article" / "pending-materials.json"
    monkeypatch.setattr("xzq.reviewer.watch.transcribe_materials", lambda materials, llm: materials)
    payload = scan_pending_materials(inbox, destination, [Material("photo", source="old.jpg")], LLMClient())
    assert payload["count"] == 1
    stored = json.loads(destination.read_text())
    assert stored["count"] == 1
    assert "不会覆盖" in stored["notice"]
