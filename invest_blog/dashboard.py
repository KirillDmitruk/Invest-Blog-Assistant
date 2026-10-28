"""Local-only read-only FastAPI dashboard. Never exposes invest credentials."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from .portfolio_store import PortfolioStore
from .integrity import check_integrity
from .storage import DraftStore

HTML_PATH = Path(__file__).parent / 'web_static' / 'index.html'


def create_app(db_path: Path, bond_target: float = 0.60, max_position: float = 0.15) -> FastAPI:
    app = FastAPI(title='Invest Blog Assistant Dashboard', docs_url=None, redoc_url=None, openapi_url=None)
    portfolio=PortfolioStore(db_path)
    drafts=DraftStore(db_path)

    @app.get('/', response_class=HTMLResponse)
    def index():
        return HTML_PATH.read_text(encoding='utf-8')

    @app.get('/api/overview')
    def overview():
        data=portfolio.get_dashboard_data(bond_target, max_position)
        if data.get('has_data'):
            audit=check_integrity(portfolio)
            data['integrity']=audit
            for issue in audit['issues']:
                data['alerts'].append({'level':'attention','text':issue})
        return data

    @app.get('/api/drafts')
    def recent_drafts():
        return [{'id':r['id'],'kind':r['kind'],'period':r['period'], 'state':r['state'],
                 'created_at':r['created_at']} for r in drafts.list_recent(15)]

    @app.get('/api/topics')
    def topics():
        with portfolio.connect() as db:
            rows = db.execute("SELECT kind,topic_key,created_at FROM editorial_topics ORDER BY id DESC LIMIT 20").fetchall()
        return [dict(row) for row in rows]

    @app.get('/api/health')
    def health():
        return {'status':'ok', 'latest_snapshot':portfolio.latest_day()}

    return app
