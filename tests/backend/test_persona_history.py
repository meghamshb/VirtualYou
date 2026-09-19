import asyncio
import json
from types import SimpleNamespace

import pytest

from virtual_you.backend.errors import ServiceError
from virtual_you.backend.persona import PersonaService
from virtual_you.backend.store import Store
from virtual_you.contracts.reporting import PersonaSeed


def test_style_history_undo_is_versioned_and_does_not_restore_examples(settings):
    settings.prepare()
    service = PersonaService(
        settings, Store(settings.data_dir / "test.sqlite"), SimpleNamespace(name="test")
    )
    profile = asyncio.run(service.create(PersonaSeed(recipient_id="person", display_name="Person")))
    # Simulate a retained example that the owner then explicitly removes.
    profile.examples = ["Private example that must never return"]
    with service.store.connection(write=True) as db:
        db.execute(
            "UPDATE personas SET payload=? WHERE recipient_id=?",
            (profile.model_dump_json(), "person"),
        )
    original = profile.style.model_copy(deep=True)
    style = original.model_copy(update={"sentence_style": "Short and direct."})
    revised = service.revise("person", 1, style, remove_examples=True)
    assert revised.version == 2
    assert [v["version"] for v in service.history("person")] == [2, 1]
    restored = service.undo("person", 2)
    assert restored.version == 3 and restored.style == original and restored.examples == []
    assert "Version: 3" in next((settings.data_dir / "personas").rglob("soul.md")).read_text()
    with pytest.raises(ServiceError, match="changed"):
        service.undo("person", 2)
    with pytest.raises(ServiceError):
        service.history("someone-else")


def test_refresh_archives_style_and_history_contains_no_examples(settings):
    settings.prepare()
    service = PersonaService(
        settings, Store(settings.data_dir / "test.sqlite"), SimpleNamespace(name="test")
    )
    seed = PersonaSeed(recipient_id="person", display_name="Person")
    asyncio.run(service.create(seed))
    asyncio.run(service.create(seed))
    assert [v["version"] for v in service.history("person")] == [2, 1]
    assert all(set(v) == {"version", "style", "created"} for v in service.history("person"))
    learned = service.formal_style().model_copy(
        update={"sentence_style": "Short, direct sentences."}
    )
    service.revise("person", 2, learned, remember_sentence_style=True)
    refreshed = asyncio.run(service.create(seed))
    assert refreshed.style.sentence_style == learned.sentence_style
    assert "Private example" not in json.dumps(service.history("person"))
