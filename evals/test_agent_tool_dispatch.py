"""Regression tests for model-selected tool arguments and permission ceilings."""
from __future__ import annotations

import pytest

from core import agent as agent_core, db, router


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.setattr(db, "_WAL_SET", False)
    db._SCHEMA_DONE.clear()
    db.init()
    yield
    db._SCHEMA_DONE.clear()


def test_yellow_agent_can_still_choose_green_tool(monkeypatch):
    name = "test_green_under_yellow"
    agent_core.TOOLS[name] = agent_core.Tool(name, "GREEN", "read a fact", lambda: "ok")
    member = agent_core.Agent("test_role", "test", "rules", (name,), "one measured result",
                              max_class="YELLOW")
    monkeypatch.setattr(router, "run", lambda *_: '{"tool":"test_green_under_yellow","args":{},"why":"fresh fact"}')
    monkeypatch.setattr(router, "model_for", lambda *_: "test-model")
    try:
        result = member.act()
    finally:
        agent_core.TOOLS.pop(name, None)
    assert result["ok"] is True
    assert result["detail"] == "ok"


def test_actor_context_and_model_arguments_reach_the_tool(monkeypatch):
    name = "test_actor_arguments"
    seen = {}

    def execute(actor, post_id: str, answer: str):
        seen.update(actor=actor, post_id=post_id, answer=answer)
        return "sent"

    agent_core.TOOLS[name] = agent_core.Tool(
        name, "YELLOW", "reply", execute, (), True)
    member = agent_core.Agent("critic_test", "test", "rules", (name,), "one reply",
                              max_class="YELLOW")
    monkeypatch.setattr(
        router, "run",
        lambda *_: '{"tool":"test_actor_arguments","args":{"post_id":"p1","answer":"specific"},"why":"answer it"}',
    )
    monkeypatch.setattr(router, "model_for", lambda *_: "test-model")
    try:
        result = member.act()
    finally:
        agent_core.TOOLS.pop(name, None)
    assert result["ok"] is True
    assert seen == {"actor": "critic_test", "post_id": "p1", "answer": "specific"}


def test_missing_required_tool_arguments_are_rejected(monkeypatch):
    name = "test_missing_arguments"
    called = False

    def execute(required: str):
        nonlocal called
        called = True

    agent_core.TOOLS[name] = agent_core.Tool(name, "GREEN", "needs input", execute)
    member = agent_core.Agent("argument_test", "test", "rules", (name,), "valid call")
    monkeypatch.setattr(router, "run", lambda *_: '{"tool":"test_missing_arguments","args":{},"why":"try"}')
    monkeypatch.setattr(router, "model_for", lambda *_: "test-model")
    try:
        result = member.act()
    finally:
        agent_core.TOOLS.pop(name, None)
    assert result["ok"] is False
    assert called is False
    assert "отсутствуют" in result["detail"]
