"""Local-only read-only FastAPI dashboard. Never exposes invest credentials."""
from __future__ import annotations

from pathlib import Path
import secrets

from fastapi import HTTPException, Request
from datetime import date, datetime, timezone as dt_timezone
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, Response

from .portfolio_store import PortfolioStore
from .integrity import check_integrity
from .storage import DraftStore
from .bond_analytics import get_bond_analytics
from .risk import analyze_portfolio
from .goals import GoalStore, evaluate_goals
from .editorial import ContentPlanner, RUBRICS
from .publication_schedule import recurring_schedule
from .performance import summarize
from .exporter import export_tables, export_zip, export_all_tables
from .placements import PlacementStore
from .tech_store import TechDraftStore
from .tech_news import TechNewsStore, CATEGORY_LABELS

HTML_PATH = Path(__file__).parent / 'web_static' / 'index.html'


def create_app(db_path: Path, bond_target: float = 0.60, max_position: float = 0.15,
               timezone: ZoneInfo | None = None,
               times: dict[str, tuple[int, int]] | None = None) -> FastAPI:
    app = FastAPI(title='Content Hub v2.0.1', docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])
    portfolio=PortfolioStore(db_path)
    drafts=DraftStore(db_path)
    tech_drafts=TechDraftStore(db_path)
    tech_news=TechNewsStore(db_path)
    placements=PlacementStore(db_path)
    goals=GoalStore(portfolio)
    planner=ContentPlanner(portfolio,drafts)
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

    @app.get('/api/projects')
    def projects():
        return {'projects':[{'id':'invest','name':'Инвестиции','enabled':True},
                            {'id':'tech','name':'Tech & Gadgets','enabled':True}],
                'tech':{'drafts':len(tech_drafts.list_recent()), 'news_collection':True,
                        'ai_enabled':False,'publishing_enabled':False}}

    @app.get('/api/tech/news')
    def tech_news_list(category: str | None = None, status: str | None = None):
        try: return {'items':tech_news.list_news(category=category,status=status),
                     'stats':tech_news.stats(),'categories':CATEGORY_LABELS,'sources':tech_news.sources()}
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc

    @app.post('/api/tech/news/{news_id}/action')
    async def tech_news_action(news_id:int,request:Request):
        require_editor(request)
        payload=await request.json()
        if not isinstance(payload,dict): raise HTTPException(422,'Ожидается объект')
        item=tech_news.get(news_id)
        if not item: raise HTTPException(404,'Новость не найдена')
        action=payload.get('action')
        if action=='draft':
            # News descriptions are source metadata, not independently verified facts.
            # No AI paraphrase or fabricated analysis; owner edits the result.
            body=(f"{item['title']}\n\n"
                  f"[Перескажи новость своими словами после проверки оригинала.]\n\n"
                  f"Источник: {item['source']} — {item['url']}")
            draft_id=tech_drafts.create(item['title'][:150],body,item['url'])
            tech_news.mark(news_id,'saved')
            return {'draft':tech_drafts.get(draft_id)}
        if action in ('dismissed','new'):
            tech_news.mark(news_id,action)
            return {'status':action}
        raise HTTPException(422,'Неизвестное действие')

    @app.get('/api/tech/drafts')
    def tech_list():
        return tech_drafts.list_recent()

    @app.post('/api/tech/drafts', status_code=201)
    async def tech_create(request: Request):
        require_editor(request)
        payload=await request.json()
        if not isinstance(payload,dict): raise HTTPException(422,'Ожидается объект')
        try:
            did=tech_drafts.create(payload.get('title'),payload.get('body'),payload.get('source_url',''))
        except (ValueError,TypeError) as exc: raise HTTPException(422,str(exc)) from exc
        return tech_drafts.get(did)

    @app.put('/api/tech/drafts/{draft_id}')
    async def tech_update(draft_id:int,request:Request):
        require_editor(request)
        payload=await request.json()
        if not isinstance(payload,dict) or type(payload.get('revision')) is not int: raise HTTPException(422,'Требуется revision')
        try:
            ok=tech_drafts.update(draft_id,payload.get('title'),payload.get('body'),payload.get('source_url',''),payload['revision'])
        except (ValueError,TypeError) as exc: raise HTTPException(422,str(exc)) from exc
        if not ok: raise HTTPException(409,'Черновик изменён или отсутствует')
        return tech_drafts.get(draft_id)

    @app.delete('/api/tech/drafts/{draft_id}',status_code=204)
    async def tech_delete(draft_id:int,request:Request):
        require_editor(request)
        payload=await request.json()
        if not isinstance(payload,dict) or type(payload.get('revision')) is not int: raise HTTPException(422,'Требуется revision')
        if not tech_drafts.delete(draft_id,payload['revision']): raise HTTPException(409,'Черновик изменён или отсутствует')

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

    def performance_report(start: str | None = None, end: str | None = None):
        try:
            beginning = date.fromisoformat(start) if start else None
            finish = date.fromisoformat(end) if end else None
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail='Дата должна быть YYYY-MM-DD') from exc
        if beginning and finish and (finish <= beginning or (finish - beginning).days > 3650):
            raise HTTPException(status_code=422, detail='Укажи период от 1 до 3650 дней')
        return summarize(portfolio, start=beginning, end=finish)

    @app.get('/api/performance')
    def performance(start: str | None = None, end: str | None = None):
        return performance_report(start, end)

    @app.get('/api/performance/export')
    def performance_export(table: str = 'all', start: str | None = None, end: str | None = None):
        report = performance_report(start, end)
        if table == 'all':
            return Response(export_zip(report, portfolio), media_type='application/zip',
                            headers={'Content-Disposition':'attachment; filename="invest-analytics-v1.5.zip"',
                                     'Cache-Control':'no-store'})
        if table not in ('performance', 'broker_snapshots', 'external_cashflows', 'current_positions', 'income_by_month'):
            raise HTTPException(status_code=422, detail='Неизвестный тип экспорта')
        filename = table+'.csv'
        return Response(export_all_tables(portfolio, report)[filename], media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="{filename}"',
                                 'Cache-Control':'no-store'})

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
        queued = {r['id'] for r in drafts.pending_publications(limit=200)}
        return [{'id':r['id'],'kind':r['kind'],'period':r['period'], 'state':r['state'],
                 'revision':r['revision'], 'queued':r['id'] in queued,
                 'scheduled_at':r['scheduled_at'],
                 'created_at':r['created_at']} for r in drafts.list_recent(100)]

    @app.get('/api/publication-routine')
    def publication_routine():
        return recurring_schedule(times, str(timezone or ZoneInfo('Europe/Moscow')))

    @app.get('/api/content-plan')
    def content_plan():
        now = datetime.now(timezone).date() if timezone else date.today()
        return planner.overview(now)

    @app.get('/api/editor/drafts/{draft_id}')
    def editor_get(draft_id: int):
        row = drafts.get(draft_id)
        if row is None or row['state'] == 'deleted':
            raise HTTPException(status_code=404, detail='Черновик не найден')
        return dict(row)

    @app.post('/api/editor/drafts', status_code=201)
    async def editor_create(request: Request):
        require_editor(request)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail='Ожидается объект')
        try:
            did = drafts.create_manual(payload.get('kind'),payload.get('period'),payload.get('body'))
        except (ValueError,TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return dict(drafts.get(did))

    @app.put('/api/editor/drafts/{draft_id}')
    async def editor_update(draft_id: int, request: Request):
        require_editor(request)
        payload = await request.json()
        if not isinstance(payload, dict) or type(payload.get('revision')) is not int:
            raise HTTPException(status_code=422, detail='Требуется revision')
        try:
            drafts.update_body(draft_id,payload.get('body'),payload['revision'])
        except RuntimeError as exc:
            raise HTTPException(status_code=409,detail=str(exc)) from exc
        except (ValueError,TypeError) as exc:
            raise HTTPException(status_code=422,detail=str(exc)) from exc
        return dict(drafts.get(draft_id))

    @app.delete('/api/editor/drafts/{draft_id}', status_code=204)
    async def editor_delete(draft_id: int, request: Request):
        require_editor(request)
        payload = await request.json()
        if not isinstance(payload, dict) or type(payload.get('revision')) is not int:
            raise HTTPException(status_code=422, detail='Требуется revision')
        row = drafts.get(draft_id)
        if not row or row['state'] == 'deleted':
            raise HTTPException(status_code=404, detail='Черновик не найден')
        if not drafts.delete_from_queue(draft_id, payload['revision']):
            raise HTTPException(status_code=409, detail='Черновик изменён или уже публикуется/опубликован')

    @app.post('/api/editor/drafts/{draft_id}/action')
    async def editor_action(draft_id: int, request: Request):
        require_editor(request)
        payload = await request.json()
        if not isinstance(payload,dict) or type(payload.get('revision')) is not int:
            raise HTTPException(status_code=422,detail='Требуется revision')
        row = drafts.get(draft_id)
        if not row:
            raise HTTPException(status_code=404,detail='Черновик не найден')
        if row['revision'] != payload['revision']:
            raise HTTPException(status_code=409,detail='Черновик изменился. Обнови страницу.')
        action = payload.get('action')
        if action in ('review','approved','rejected'):
            if not drafts.change_state(draft_id, action, expected_revision=payload['revision']):
                raise HTTPException(status_code=409,detail='Недоступный переход или устаревшая версия')
        elif action == 'unschedule':
            if row['state'] != 'scheduled' or not drafts.change_state(draft_id,'approved',expected_revision=payload['revision']):
                raise HTTPException(status_code=409,detail='Пост не запланирован')
        elif action == 'schedule':
            value = payload.get('when')
            if not isinstance(value,str):
                raise HTTPException(status_code=422,detail='Укажи дату YYYY-MM-DDTHH:MM')
            try:
                when = datetime.strptime(value,'%Y-%m-%dT%H:%M').replace(tzinfo=timezone or ZoneInfo('Europe/Moscow'))
                if when<=datetime.now(when.tzinfo):
                    raise ValueError('Дата должна быть в будущем')
            except ValueError as exc:
                raise HTTPException(status_code=422,detail=str(exc)) from exc
            if not drafts.schedule(draft_id,when.astimezone(dt_timezone.utc).isoformat(),expected_revision=payload['revision']):
                raise HTTPException(status_code=409,detail='Сначала одобри актуальную версию поста')
        elif action == 'queue_publish':
            if not drafts.queue_publish(draft_id):
                raise HTTPException(status_code=409,detail='Для публикации нужен одобренный текст длиной до 4096 символов')
        else:
            raise HTTPException(status_code=422,detail='Неизвестное действие')
        return dict(drafts.get(draft_id))

    @app.get('/api/placements')
    def upcoming_placements():
        now = datetime.now(timezone).date() if timezone else date.today()
        return {'items': placements.upcoming(now), 'sources': placements.status(),
                'timezone': str(timezone or ZoneInfo('Europe/Moscow'))}

    @app.post('/api/placements/{placement_id}/draft', status_code=201)
    def placement_to_draft(placement_id: int, request: Request):
        require_editor(request)
        try:
            did = placements.create_draft(placement_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return dict(drafts.get(did))

    @app.get('/api/topics')
    def topics():
        with portfolio.connect() as db:
            rows = db.execute("SELECT kind,topic_key,created_at FROM editorial_topics ORDER BY id DESC LIMIT 20").fetchall()
        return [dict(row) for row in rows]

    @app.get('/api/health')
    def health():
        return {'status':'ok', 'latest_snapshot':portfolio.latest_day()}

    return app
