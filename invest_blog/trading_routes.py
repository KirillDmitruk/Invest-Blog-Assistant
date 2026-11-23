"""Local PAPER lab API. No route can choose a live mode or submit a broker order."""
from decimal import Decimal
import os
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .trading_lab import TradingLab, LabConflict, TZ, utcnow
from .trading_market import FuturesMarket
from .trading_ai import check_xai_access


Number = Annotated[Decimal, Field(ge=0, le=1000000, allow_inf_nan=False, max_digits=14, decimal_places=6)]


class Risk(BaseModel):
    model_config = ConfigDict(extra='forbid')
    risk_pct: Annotated[Decimal, Field(gt=0, le=5, allow_inf_nan=False)] = Decimal('1')
    daily_loss_pct: Annotated[Decimal, Field(gt=0, le=10, allow_inf_nan=False)] = Decimal('3')
    max_drawdown_pct: Annotated[Decimal, Field(gt=0, le=30, allow_inf_nan=False)] = Decimal('10')
    max_margin_pct: Annotated[Decimal, Field(gt=0, le=80, allow_inf_nan=False)] = Decimal('30')
    max_positions: int = Field(default=2, ge=1, le=5, strict=True)
    max_contracts: int = Field(default=1, ge=1, le=10, strict=True)
    commission: Number = Decimal('2')
    slippage_ticks: int = Field(default=1, ge=0, le=20, strict=True)
    margin_buffer_pct: Annotated[Decimal, Field(ge=0, le=100, allow_inf_nan=False)] = Decimal('25')


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)
    budget: Annotated[Decimal, Field(gt=0, le=1000000, decimal_places=2, allow_inf_nan=False)]
    enabled: bool = Field(strict=True)
    strategy: Literal['ai', 'rules', 'connector']
    risk: Risk


class Search(BaseModel):
    query: str = Field(min_length=2, max_length=60)


class ContractInput(BaseModel):
    uid: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')


def market():
    return FuturesMarket(os.getenv('TINVEST_TOKEN', ''), TZ)


def register_trading_routes(app, path, require_editor):
    lab = TradingLab(path)
    router = APIRouter(prefix='/api/trading')
    from .tinvest_paper_routes import FuturesDashboard
    cloud = FuturesDashboard(path)
    original_editor = require_editor
    def require_editor(request):
        original_editor(request)
        if os.getenv('TINVEST_PAPER_CLOUD_ENABLED') == 'true':
            raise HTTPException(409, 'Виртуальным счётом управляет облако. Локальные команды отключены.')

    from .trading_connector import AnalysisDesk, Prepare
    desk = AnalysisDesk(path)

    @router.get('/analysis')
    def analysis_status():
        return desk.local_status()

    @router.post('/analysis')
    def prepare_analysis(payload: Prepare, request: Request):
        require_editor(request)
        try:
            return desk.prepare(market(), payload.scope)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get('/overview')
    def overview(refresh:bool=False):
        if os.getenv('TINVEST_PAPER_CLOUD_ENABLED') == 'true':
            try:return cloud.overview(force=refresh)
            except ValueError as exc:raise HTTPException(503,str(exc)) from exc
        return lab.overview()

    @router.put('/settings')
    def settings(payload: SettingsInput, request: Request):
        require_editor(request)
        values = payload.model_dump(mode='json')
        revision = values.pop('revision')
        try:
            return lab.configure(values, revision)
        except LabConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post('/pause')
    def pause(request: Request):
        require_editor(request)
        lab.pause()
        return {'status': 'paused'}

    @router.post('/search')
    def search(payload: Search, request: Request):
        require_editor(request)
        try:
            return {'items': market().search(payload.query.strip())}
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc

    @router.post('/watchlist', status_code=201)
    def watch(payload: ContractInput, request: Request):
        require_editor(request)
        try:
            contract = market().contract(payload.uid)
            from .model import iso_dt
            if iso_dt(contract['expires_at']) <= utcnow():
                raise ValueError('Контракт уже истёк. Выбери действующий выпуск.')
            lab.watch(contract)
            return contract
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.delete('/watchlist/{uid}')
    def unwatch(uid: str, request: Request):
        require_editor(request)
        try:
            lab.unwatch(uid)
            return {'status': 'removed'}
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post('/cycle')
    def cycle(request: Request):
        require_editor(request)
        try:
            return lab.cycle(market())
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc

    @router.post('/positions/{uid}/close')
    async def close(uid: str, request: Request):
        require_editor(request)
        data = await request.json()
        if not isinstance(data, dict) or data.get('confirm') != 'PAPER_CLOSE':
            raise HTTPException(422, 'Подтверди закрытие виртуальной позиции.')
        from starlette.concurrency import run_in_threadpool
        try:
            await run_in_threadpool(lab.close_position, uid, market())
            return {'status': 'closed'}
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post('/ai-check')
    def ai_check(request: Request):
        require_editor(request)
        try:
            return check_xai_access()
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc

    app.include_router(router)


def serve(path):
    """Periodic paper analysis. Start does not enable the account or any broker trading."""
    if os.getenv('TINVEST_PAPER_CLOUD_ENABLED') == 'true':
        raise SystemExit('Торговый бот работает в облаке. Локальный процесс не требуется.')
    from apscheduler.schedulers.blocking import BlockingScheduler
    import logging
    lab = TradingLab(path)
    logger = logging.getLogger(__name__)
    def tick():
        lab.heartbeat()
        state = lab.overview()['state']
        if not state['watchlist']:
            return
        try:
            logger.info('PAPER lab: %s', lab.cycle(market())['note'])
        except Exception as exc:
            logger.warning('PAPER lab: цикл пропущен (%s)', type(exc).__name__)
    scheduler = BlockingScheduler(timezone=TZ)
    scheduler.add_job(tick, 'interval', minutes=5, max_instances=1, coalesce=True, misfire_grace_time=120)
    lab.heartbeat()
    logger.info('PAPER lab запущена; реальные заявки недоступны. Цикл раз в 5 минут.')
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        if scheduler.running:
            scheduler.shutdown(wait=False)
