"""Local-only read-only FastAPI dashboard. Never exposes invest credentials."""
from __future__ import annotations

from pathlib import Path
import secrets

from fastapi import HTTPException, Request
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse

from .portfolio_store import PortfolioStore
from .integrity import check_integrity
from .storage import DraftStore
from .bond_analytics import get_bond_analytics
from .risk import analyze_portfolio
from .goals import GoalStore, evaluate_goals

HTML_PATH = Path(__file__).parent / 'web_static' / 'index.html'


def create_app(db_path: Path, bond_target: float = 0.60, max_position: float = 0.15,
               timezone: ZoneInfo | None = None) -> FastAPI:
    app = FastAPI(title='Invest Blog Assistant Dashboard', docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])
    portfolio=PortfolioStore(db_path)
    drafts=DraftStore(db_path)
    goals=GoalStore(portfolio)
    csrf_nonce=secrets.token_urlsafe(32)

    def require_editor(request: Request):
        # No automatic CORS grants. Custom header requires same-origin JS to
        # fetch the nonce; ordinary cross-site forms cannot supply it.
        if request.headers.get('x-csrf-token') != csrf_nonce:
            raise HTTPException(status_code=403, detail='Неверный токен редактора')
        origin = request.headers.get('origin')
        if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            raise HTTPException(status_code=403, detail='Запрос не со страницы приложения')


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

    @app.get('/api/bonds')
    def bonds():
        return get_bond_analytics(portfolio, today=datetime.now(timezone).date() if timezone else None)

    @app.get('/api/risk')
    def risk():
        return analyze_portfolio(portfolio, bond_target=bond_target,
                                 max_position=max_position,
                                 today=datetime.now(timezone).date() if timezone else None)

    @app.get('/api/goals')
    def list_goals():
        return evaluate_goals(goals)

    @app.get('/api/editor-token')
    def editor_token():
        return {'token': csrf_nonce}

    @app.post('/api/goals', status_code=201)
    async def add_goal(request: Request):
        require_editor(request)
        try:
            return goals.upsert(await request.json())
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put('/api/goals/{goal_id}')
    async def edit_goal(goal_id: int, request: Request):
        require_editor(request)
        try:
            return goals.upsert(await request.json(), goal_id=goal_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='Цель не найдена') from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete('/api/goals/{goal_id}', status_code=204)
    def delete_goal(goal_id: int, request: Request):
        require_editor(request)
        try:
            goals.delete(goal_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='Цель не найдена') from exc

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
