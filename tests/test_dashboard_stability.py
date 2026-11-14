"""Regression coverage for safe calendar completion and resilient dashboard boot."""
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from invest_blog.dashboard import create_app
from invest_blog.model import Draft
from invest_blog.storage import DraftStore


def test_calendar_done_endpoint_preserves_draft(tmp_path):
    path = tmp_path / "dashboard.db"
    drafts = DraftStore(path)
    day = date.today() + timedelta(days=(4 - date.today().weekday()) % 7)
    period = (day - timedelta(days=day.weekday())).isoformat()
    draft_id, _ = drafts.insert(Draft("glossary", period, "Мой готовый пост"), scheduled=True)
    with TestClient(create_app(path)) as client:
        token = client.get("/api/editor-token").json()["token"]
        response = client.post(
            "/api/editor/calendar-status",
            headers={"X-CSRF-Token": token},
            json={"day": day.isoformat(), "kind": "glossary", "status": "done"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "done"
        assert drafts.get(draft_id)["body"] == "Мой готовый пост"
        assert not any(
            row["kind"] == "glossary" and row["day"] == day.isoformat()
            for row in client.get("/api/content-plan").json()["schedule"]
        )


def test_dashboard_boot_keeps_independent_sections_on_api_failure():
    html = (Path(__file__).resolve().parents[1]
            / "invest_blog" / "web_static" / "index.html").read_text(encoding="utf-8")
    assert "Promise.allSettled(endpoints.map" in html
    assert "if(state.risk)renderRisk()" in html
    assert "if(state.goals)renderGoals()" in html
    assert "if(state.performance)renderPerformance()" in html
    assert "Повторить загрузку" in html
    assert "Черновик будет полностью удалён" not in html
