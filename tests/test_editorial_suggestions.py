"""Regression tests for persistent editorial suggestion actions."""
from datetime import date

import pytest

from invest_blog.storage import DraftStore
from invest_blog.editorial_workflow import EditorialWorkflow
from invest_blog.placements import PlacementStore


def test_glossary_draft_is_idempotent_and_done_survives_restart(tmp_path):
    path = tmp_path / "editorial.db"
    DraftStore(path)
    workflow = EditorialWorkflow(path)
    with workflow.connect() as db:
        db.execute(
            "INSERT INTO editorial_glossary_ideas(topic,status,week) VALUES(?,'reserve',?)",
            ("Накопленный купонный доход", "2026-11-09"),
        )

    first = workflow.glossary_draft("Накопленный купонный доход", date(2026, 11, 14))
    second = workflow.glossary_draft("Накопленный купонный доход", date(2026, 11, 14))
    assert first == second
    assert "Накопленный купонный доход" in DraftStore(path).get(first)["body"]
    assert workflow.ideas()[0]["draft_id"] == first

    workflow.glossary_done("Накопленный купонный доход")
    reloaded = EditorialWorkflow(path)
    assert reloaded.ideas() == []
    assert DraftStore(path).get(first)["state"] == "new"
    with pytest.raises(ValueError):
        reloaded.glossary_draft("Накопленный купонный доход")


def test_glossary_deleted_draft_can_be_recreated(tmp_path):
    path = tmp_path / "editorial.db"
    drafts = DraftStore(path)
    workflow = EditorialWorkflow(path)
    with workflow.connect() as db:
        db.execute(
            "INSERT INTO editorial_glossary_ideas(topic,status,week) VALUES(?,'reserve',?)",
            ("Кредитный рейтинг эмитента", "2026-11-09"),
        )
    first = workflow.glossary_draft("Кредитный рейтинг эмитента")
    assert drafts.delete_from_queue(first, drafts.get(first)["revision"])
    second = workflow.glossary_draft("Кредитный рейтинг эмитента")
    assert second != first


def test_radar_done_preserves_source_and_draft(tmp_path):
    path = tmp_path / "editorial.db"
    DraftStore(path)
    store = PlacementStore(path)
    store.save("tinvest", [{
        "source": "tinvest",
        "source_key": "RU000TEST123",
        "name": "Тестовый выпуск",
        "isin": "RU000TEST123",
        "placement_date": "2099-01-01",
    }])
    issue_id = store.upcoming()[0]["id"]
    draft_id = store.create_draft(issue_id)
    store.mark_done(issue_id)

    assert issue_id in PlacementStore(path).done_ids()
    assert store.get(issue_id) is not None
    assert DraftStore(path).get(draft_id)["state"] == "new"
    with pytest.raises(ValueError):
        store.create_draft(issue_id)
