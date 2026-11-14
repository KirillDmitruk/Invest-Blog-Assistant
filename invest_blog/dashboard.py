"""Local-only read-only FastAPI dashboard. Never exposes invest credentials."""
from __future__ import annotations

from pathlib import Path
import secrets

from fastapi import HTTPException, Request
from datetime import date, datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from .portfolio_store import PortfolioStore
from .integrity import check_integrity
from .storage import DraftStore
from .bond_analytics import get_bond_analytics
from .risk import analyze_portfolio
from .goals import GoalStore, evaluate_goals
from .editorial import ContentPlanner, RUBRICS, period_for
from .editorial_workflow import EditorialWorkflow
from .publication_schedule import recurring_schedule
from .performance import summarize
from .exporter import export_tables, export_zip, export_all_tables
from .placements import PlacementStore
from .creator_studio import CreatorStore, PROJECTS, STATUSES
from .tech_store import TechDraftStore
from .tech_news import TechNewsStore, CATEGORY_LABELS
from .posts import PostGenerator, previous_workweek
from .blog_report import build_blog_report
from .tinvest import TInvest
from .config import Settings
from .model import Draft
from .monthly_investments import MonthlyPlans, fetch_cbr_usd

HTML_PATH = Path(__file__).parent / 'web_static' / 'index.html'


def create_app(db_path: Path, bond_target: float = 0.60, max_position: float = 0.15,
               timezone: ZoneInfo | None = None,
               times: dict[str, tuple[int, int]] | None = None) -> FastAPI:
    app = FastAPI(title='Content Hub v2.4', docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])
    portfolio=PortfolioStore(db_path)
    drafts=DraftStore(db_path)
    tech_drafts=TechDraftStore(db_path)
    creator=CreatorStore(db_path)
    tech_news=TechNewsStore(db_path)
    placements=PlacementStore(db_path)
    goals=GoalStore(portfolio)
    planner=ContentPlanner(portfolio,drafts)
    workflow=EditorialWorkflow(db_path)
    csrf_nonce=secrets.token_urlsafe(32)
    monthly_plans=MonthlyPlans(db_path)

    def require_editor(request: Request):
        # No automatic CORS grants. Custom header requires same-origin JS to
        # fetch the nonce; ordinary cross-site forms cannot supply it.
        if request.headers.get('x-csrf-token') != csrf_nonce:
            raise HTTPException(status_code=403, detail='Неверный токен редактора')
        origin = request.headers.get('origin')
        if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            raise HTTPException(status_code=403, detail='Запрос не со страницы приложения')

    from .iis_routes import register_iis_routes
    register_iis_routes(app, db_path, require_editor)
    from .editor_tools import register_editor_tools
    register_editor_tools(app, require_editor)
    from .walt_routes import register_walt_routes
    register_walt_routes(app, db_path, require_editor)
    app.mount('/static', StaticFiles(directory=HTML_PATH.parent), name='static')


    @app.get('/', response_class=HTMLResponse)
    def index():
        return HTML_PATH.read_text(encoding='utf-8')

    @app.get('/api/investment-plan/fx')
    def investment_fx():
        try: return fetch_cbr_usd()
        except Exception as exc: raise HTTPException(503, 'Курс ЦБ недоступен: '+str(exc)) from exc

    @app.get('/api/investment-contributions/{month}')
    def contributions_get(month: str):
        try: return monthly_plans.contributions(month)
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc

    @app.post('/api/investment-contributions/{month}')
    async def contributions_add(month: str, request: Request):
        require_editor(request)
        data=await request.json()
        try: return monthly_plans.add_contribution(month,data['amount'],data['received_on'],data.get('note',''))
        except (ValueError,KeyError,TypeError) as exc: raise HTTPException(422,str(exc)) from exc

    @app.delete('/api/investment-contributions/{month}/{contribution_id}')
    async def contributions_delete(month: str, contribution_id: int, request: Request):
        require_editor(request)
        try: return monthly_plans.delete_contribution(month,contribution_id)
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc

    @app.post('/api/investment-contributions/{month}/close')
    async def contributions_close(month: str, request: Request):
        require_editor(request)
        data=await request.json()
        try:
            fx=fetch_cbr_usd()
            return {'plan':monthly_plans.close_contributions(month,data['cushion'],fx)}
        except (ValueError,KeyError,TypeError) as exc: raise HTTPException(422,str(exc)) from exc
        except Exception as exc: raise HTTPException(503,'Курс ЦБ недоступен: '+str(exc)) from exc

    @app.get('/api/investment-plan/{month}')
    def investment_plan(month: str):
        try: return {'plan': monthly_plans.get(month)}
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc

    @app.post('/api/investment-plan/{month}')
    async def investment_plan_create(month: str, request: Request):
        require_editor(request)
        payload=await request.json()
        try:
            fx=fetch_cbr_usd()
            return {'plan':monthly_plans.save(month,payload['amount'],payload['cushion'],fx)}
        except (ValueError,KeyError,TypeError) as exc:raise HTTPException(422,str(exc)) from exc
        except Exception as exc:raise HTTPException(503,'Курс ЦБ недоступен: '+str(exc)) from exc

    @app.post('/api/investment-plan/{month}/purchase')
    async def investment_plan_purchase(month: str, request: Request):
        require_editor(request)
        payload=await request.json()
        if type(payload.get('done')) is not bool: raise HTTPException(422,'Нужен булев done')
        try:return {'plan':monthly_plans.mark(month,payload['id'],payload['done'])}
        except (ValueError,KeyError,TypeError) as exc:raise HTTPException(422,str(exc)) from exc

    @app.get('/api/projects')
    def projects():
        return {'projects':[{'id':'invest','name':'Инвестиции','enabled':True},
                            {'id':'tech','name':'Tech & Gadgets','enabled':True}],
                'tech':{'drafts':len(tech_drafts.list_recent()), 'news_collection':True,
                        'ai_enabled':False,'publishing_enabled':False}}

    @app.get('/api/creator/studio')
    def creator_studio(start: str | None = None):
        try:
            anchor=date.fromisoformat(start) if start else datetime.now(timezone or ZoneInfo('Europe/Moscow')).date()
        except ValueError as exc:
            raise HTTPException(422,'Неверная дата') from exc
        return {'projects': [{'id':key,'name':v[0],'subtitle':v[1],'description':v[2]} for key,v in PROJECTS.items()],
                'statuses': list(STATUSES), 'posts':creator.list(anchor-timedelta(days=anchor.weekday()))}

    @app.post('/api/creator/seed')
    async def creator_seed(request: Request):
        require_editor(request)
        payload=await request.json()
        try: anchor=date.fromisoformat(payload.get('start',date.today().isoformat()))
        except (ValueError,TypeError,AttributeError) as exc: raise HTTPException(422,'Неверная дата') from exc
        return {'created':creator.seed(anchor)}

    @app.put('/api/creator/posts/{post_id}')
    async def creator_update(post_id:int,request:Request):
        require_editor(request)
        data=await request.json()
        if not isinstance(data,dict): raise HTTPException(422,'Ожидается объект')
        try: result=creator.update(post_id,data)
        except (ValueError,TypeError) as exc: raise HTTPException(422,str(exc)) from exc
        if result is None: raise HTTPException(409,'План изменён или запись не найдена')
        return result

    @app.get('/api/tech/publishing')
    def tech_publishing():
        from .tech_publish_dashboard import overview
        return overview(db_path, timezone)

    @app.post('/api/tech/publishing/now')
    async def tech_publish_now(request: Request):
        require_editor(request)
        data = await request.json()
        if not isinstance(data, dict) or data.get('confirm') != 'PUBLISH':
            raise HTTPException(422, 'Подтверди публикацию словом PUBLISH')
        from .tech_publish_dashboard import dispatch
        try:
            return dispatch(db_path, timezone, limit=1)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

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

    @app.get('/api/blog-report/accounts')
    def blog_report_accounts():
        settings = Settings.load()
        return {
            'accounts': [{'id': aid, 'label': 'Счёт …' + aid[-4:]} for aid in settings.account_ids],
            'default_account_id': (
                settings.iis_review_account_id if settings.iis_review_account_id in settings.account_ids
                else settings.account_ids[0] if len(settings.account_ids) == 1 else None
            ),
        }

    @app.post('/api/blog-report')
    async def blog_report(request: Request):
        """Fetch fresh executed operations for one configured account, read-only."""
        require_editor(request)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(422, detail='Ожидается объект')
        try:
            start = date.fromisoformat(payload.get('start', ''))
            end = date.fromisoformat(payload.get('end', ''))
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, detail='Укажи корректные даты начала и окончания') from exc
        settings = Settings.load()
        today = datetime.now(settings.timezone).date()
        if start > end or (end - start).days > 365 or end > today:
            raise HTTPException(422, detail='Период: от 1 до 366 дней, без будущих дат')
        account_id = payload.get('account_id')
        if not isinstance(account_id, str) or account_id not in settings.account_ids:
            raise HTTPException(422, detail='Выбери один из настроенных счетов')
        if not settings.invest_token:
            raise HTTPException(503, detail='TINVEST_TOKEN не настроен')
        try:
            api = TInvest(settings.invest_token, settings.timezone)
            operations = api.operations(account_id, start, end)
            def describe(op):
                instrument = api.instrument(uid=op.get('instrumentUid') or '',
                                            figi=op.get('figi') or '')
                return instrument.name + instrument.tag
            report = build_blog_report(operations, start, end, settings.timezone, describe)
        except Exception as exc:
            # Never expose credentials or raw broker error responses to the browser.
            raise HTTPException(502, detail='Не удалось получить операции T-Invest: ' +
                                type(exc).__name__) from exc
        report['draft_supported'] = len(report['text']) <= 10000
        return report

    @app.get('/api/drafts')
    def recent_drafts(include_body: bool = False):
        queued = {r['id'] for r in drafts.pending_publications(limit=200)}
        return [{'id':r['id'],'kind':r['kind'],'period':r['period'], 'state':r['state'],
                 'revision':r['revision'], 'queued':r['id'] in queued,
                 'scheduled_at':r['scheduled_at'],
                 'created_at':r['created_at'],
                 **({'body':r['body']} if include_body else {})} for r in drafts.list_recent(100)]

    @app.get('/api/publication-routine')
    def publication_routine():
        return recurring_schedule(times, str(timezone or ZoneInfo('Europe/Moscow')))

    @app.get('/api/content-plan')
    def content_plan():
        now = datetime.now(timezone).date() if timezone else date.today()
        result=planner.overview(now)
        # Radar is event-driven, not a weekly slot. Do not offer old securities or stale listings.
        issues=placements.upcoming(now,limit=100)
        ranked=[]
        with drafts._connect() as db:
            ids={r['placement_id']:r['draft_id'] for r in db.execute(
                "SELECT p.placement_id,p.draft_id FROM placement_drafts p JOIN drafts d ON d.id=p.draft_id WHERE d.state!='deleted'")}
        seen=set()
        completed=placements.done_ids()
        for issue in issues:
            if issue['id'] in completed:continue
            key=(issue.get('isin') or issue.get('name') or '').strip().upper()
            if not key or key in seen:continue
            seen.add(key)
            ranked.append({'id':issue['id'],'name':issue['name'],'isin':issue.get('isin'),
                  'placement_date':issue['placement_date'],'source_label':issue.get('source','источник'),
                  'draft_id':ids.get(issue['id'])})
            if len(ranked)>=8:break
        result['radar_suggestions']=ranked
        result['paper_candidates']=[]  # Old technical "recent buy/coupon" tips are intentionally hidden.
        return result

    @app.post('/api/editor/calendar-draft', status_code=200)
    async def calendar_draft(request: Request):
        """Open or generate a calendar item, preserving existing edited posts."""
        require_editor(request)
        payload = await request.json()
        if not isinstance(payload, dict) or not isinstance(payload.get('day'), str) or not isinstance(payload.get('kind'), str):
            raise HTTPException(422, detail='Нужны дата и рубрика')
        try:
            target = date.fromisoformat(payload['day'])
        except ValueError as exc:
            raise HTTPException(422, detail='Неверная дата') from exc
        kind = payload['kind']
        now = datetime.now(timezone).date() if timezone else date.today()
        if (target - now).days not in range(35):
            raise HTTPException(422, detail='Выбери запись из календаря на ближайшие 35 дней')
        if not any(x['day'] == target.isoformat() and x['kind'] == kind for x in planner.calendar(now)):
            raise HTTPException(422, detail='Такой рубрики в календаре нет')
        if workflow.states().get((target.isoformat(),kind))=='done':
            raise HTTPException(409,detail='Пост отмечен как написанный')
        period = period_for(kind, target)
        periods = [period]
        if kind == 'weekly':
            start, end = previous_workweek(target)
            periods.append(f'{start.isoformat()}_{end.isoformat()}')
        with drafts._connect() as db:
            row = db.execute("SELECT * FROM drafts WHERE kind=? AND period IN ("+','.join('?' for _ in periods)+") AND state!='deleted' ORDER BY id DESC LIMIT 1",(kind,*periods)).fetchone()
        if row:
            return {'draft':dict(row),'created':False}
        settings = Settings.load()
        if kind != 'glossary' and (not settings.invest_token or not settings.account_ids):
            raise HTTPException(503, detail='Для генерации этой рубрики нужны TINVEST_TOKEN и TINVEST_ACCOUNT_IDS')
        try:
            generator = PostGenerator(TInvest(settings.invest_token, settings.timezone),
                                      settings.account_ids or ('glossary-only',), settings.cbr_path)
            if kind=='glossary' and workflow.selection(target):
                from .lessons import TERMS
                topic=workflow.selection(target)
                known=dict(TERMS)
                body=known.get(topic, f"Тема: {topic}\n\n[Объяснить простыми словами.]\n\n[Привести пример.]\n\n[Объяснить, почему это важно.]\n\n[Указать распространённую ошибку и вывод.]")
                generated=Draft('glossary',period,f"Ликбез за 2 минуты: {topic}\n\n{body}")
            else:
                generated = generator.generate(kind,target)
            if not generated.body.strip():
                raise ValueError('Генератор вернул пустой текст')
        except Exception as exc:
            # Do not save an empty or partially generated draft.
            raise HTTPException(503, detail='Не удалось сформировать пост: '+str(exc)[:240]) from exc
        did, created = drafts.insert(Draft(kind,period,generated.body),scheduled=True)
        result = drafts.get(did)
        if result['state']=='deleted':
            raise HTTPException(409, detail='Публикация удалена из очереди; создай новый черновик вручную')
        return {'draft':dict(result),'created':created}

    @app.post('/api/editor/calendar-status')
    async def calendar_status(request: Request):
        require_editor(request)
        payload=await request.json()
        if not isinstance(payload,dict):raise HTTPException(422,'Ожидается объект')
        try:
            day=date.fromisoformat(payload.get('day',''))
            now=datetime.now(timezone).date() if timezone else date.today()
            if not -35 <= (day-now).days <= 35:raise ValueError('Дата вне календаря')
            state=workflow.progress(day.isoformat(),payload.get('kind'),payload.get('status'),payload.get('revision'))
        except (ValueError,TypeError) as exc:raise HTTPException(409,detail=str(exc)) from exc
        return {'status':state}

    @app.post('/api/editor/glossary-select')
    async def glossary_select(request: Request):
        require_editor(request)
        payload=await request.json()
        if not isinstance(payload,dict):raise HTTPException(422,'Ожидается объект')
        now=datetime.now(timezone).date() if timezone else date.today()
        workflow.refresh_ideas(now)
        try:workflow.select(payload.get('topic'),now)
        except (ValueError,TypeError) as exc:raise HTTPException(409,detail=str(exc)) from exc
        return {'selected':workflow.selection(now),'remaining':workflow.ideas()}

    @app.post('/api/editor/glossary-draft', status_code=201)
    async def glossary_idea_draft(request: Request):
        require_editor(request)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(422, detail='Ожидается объект')
        now = datetime.now(timezone).date() if timezone else date.today()
        try:
            did = workflow.glossary_draft(payload.get('topic'), now)
        except (ValueError, TypeError) as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return dict(drafts.get(did))

    @app.post('/api/editor/glossary-done')
    async def glossary_idea_done(request: Request):
        require_editor(request)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(422, detail='Ожидается объект')
        try:
            workflow.glossary_done(payload.get('topic'))
        except (ValueError, TypeError) as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return {'status': 'done'}

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

    @app.post('/api/placements/{placement_id}/done')
    def placement_idea_done(placement_id: int, request: Request):
        require_editor(request)
        try:
            placements.mark_done(placement_id)
        except ValueError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        return {'status': 'done'}

    @app.get('/api/topics')
    def topics():
        with portfolio.connect() as db:
            rows = db.execute("SELECT kind,topic_key,created_at FROM editorial_topics ORDER BY id DESC LIMIT 20").fetchall()
        return [dict(row) for row in rows]

    @app.get('/api/health')
    def health():
        return {'status':'ok', 'latest_snapshot':portfolio.latest_day()}

    return app

