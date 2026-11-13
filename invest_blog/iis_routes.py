"""Local review endpoints. Same editor token/origin protection as the dashboard."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse

from .config import Settings
from .iis_review import ReviewStore, analyze_iis, run_review
from .placements import PlacementStore
from .portfolio_store import PortfolioStore


def register_iis_routes(app, db_path, require_editor):
    reviews = ReviewStore(db_path)

    def configuration():
        settings = Settings.load()
        # Honor the database actually used by this dashboard (including tests).
        from dataclasses import replace
        return replace(settings, db_path=Path(db_path))

    def selected(settings, account_id):
        value = account_id or settings.iis_review_account_id or (settings.account_ids[0] if len(settings.account_ids) == 1 else None)
        if value not in settings.account_ids:
            raise HTTPException(422, 'Выбери один из настроенных счетов')
        return value

    @app.get('/iis', response_class=HTMLResponse)
    def iis_page():
        return (Path(__file__).parent / 'web_static' / 'iis.html').read_text(encoding='utf-8')

    @app.get('/api/iis-review')
    def review_get(account_id: str | None = None):
        settings = configuration()
        common = {'accounts': [{'id': a, 'label': 'Счёт …' + a[-4:]} for a in settings.account_ids],
                  'policy': reviews.policy(), 'job': reviews.job(),
                  'schedule': {'enabled': settings.iis_review_enabled, 'interval_days': settings.iis_review_interval_days,
                               'account_id': settings.iis_review_account_id or (settings.account_ids[0] if len(settings.account_ids) == 1 else None),
                               'note': 'Проверка каждые 6 часов при работающем боте; анализ, если прошло заданное число дней. Без бота — кнопка обновления.'}}
        if not settings.account_ids:
            return dict(common, has_data=False, message='Заполни TINVEST_ACCOUNT_IDS в .env')
        if not account_id and not settings.iis_review_account_id and len(settings.account_ids) != 1:
            return dict(common, has_data=False, message='Выбери счёт для разбора')
        aid = selected(settings, account_id)
        saved = reviews.latest(aid)
        # Recalculate the local view after manual evidence/policy edits, without
        # falsely claiming that a fresh API review has been performed.
        report = analyze_iis(PortfolioStore(db_path), aid, today=datetime.now(settings.timezone).date(),
                             policy=reviews.policy(), evidence=reviews.evidence(),
                             placements=PlacementStore(db_path).upcoming(datetime.now(settings.timezone).date()), previous=saved,
                             issuer_data=reviews.issuer_data(datetime.now(settings.timezone).date()),
                             placement_sources=PlacementStore(db_path).status(), refresh_warnings=(saved or {}).get('sync_warnings', []))
        if saved:
            report.update(account=saved.get('account'), created_at=saved['created_at'], run_id=saved['run_id'],
                          sync_warnings=saved.get('sync_warnings', []), placement_sources=saved.get('placement_sources', []))
        next_at = (datetime.fromisoformat(saved['created_at']) + timedelta(days=settings.iis_review_interval_days)).isoformat() if saved else None
        return dict(report, **common, account_id=aid, history=reviews.history(aid), next_at=next_at,
                    last_changes=(saved or {}).get('changes', []))

    def update(settings, aid):
        try:
            run_review(settings, account_id=aid)
        except Exception as exc:
            logging.getLogger(__name__).warning('IIS review failed: %s', type(exc).__name__)

    @app.post('/api/iis-review/run', status_code=202)
    async def review_run(request: Request, background_tasks: BackgroundTasks):
        require_editor(request)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(422, 'Нужен объект')
        settings = configuration()
        aid = selected(settings, payload.get('account_id'))
        if not settings.invest_token:
            raise HTTPException(422, 'Не задан TINVEST_TOKEN')
        if reviews.job()['running']:
            raise HTTPException(409, 'Анализ уже выполняется')
        background_tasks.add_task(update, settings, aid)
        return {'accepted': True}

    @app.put('/api/iis-review/policy')
    async def policy_update(request: Request):
        require_editor(request)
        try:
            return reviews.save_policy(await request.json())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.put('/api/iis-review/evidence/{instrument_id}')
    async def evidence_update(instrument_id: str, request: Request):
        require_editor(request)
        try:
            return reviews.save_evidence(instrument_id, await request.json())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
