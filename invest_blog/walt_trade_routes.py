"""Explicit manual trading UI routes. No model can call these routes or confirm an order."""
from decimal import Decimal
from typing import Literal,Annotated
from fastapi import APIRouter,Request,HTTPException
from pydantic import BaseModel,ConfigDict,Field,model_validator
from starlette.concurrency import run_in_threadpool
from .walt_agent import WaltAgent
from .walt_trading import WaltTrading


class Strict(BaseModel):model_config=ConfigDict(extra='forbid')
class Limits(Strict):
    revision:int=Field(ge=1,strict=True)
    leverage:int=Field(ge=1,le=5,strict=True)
    risk_pct:Annotated[Decimal,Field(gt=0,le=3,allow_inf_nan=False)]
    portfolio_risk_pct:Annotated[Decimal,Field(gt=0,le=10,allow_inf_nan=False)]
    margin_free_pct:Annotated[Decimal,Field(gt=0,le=25,allow_inf_nan=False)]
    margin_equity_pct:Annotated[Decimal,Field(gt=0,le=35,allow_inf_nan=False)]
    min_margin:Annotated[Decimal,Field(ge=2,le=100,allow_inf_nan=False)]
    max_margin:Annotated[Decimal,Field(ge=2,le=10000,allow_inf_nan=False)]
    stop_pct:Annotated[Decimal,Field(ge=1,le=10,allow_inf_nan=False)]
    take_pct:Annotated[Decimal,Field(ge=2,le=50,allow_inf_nan=False)]
    max_positions:int=Field(ge=1,le=4,strict=True)
    max_group_positions:int=Field(ge=1,le=2,strict=True)
    max_drift_pct:Annotated[Decimal,Field(gt=0,le=1,allow_inf_nan=False)]
    @model_validator(mode='after')
    def bounds(self):
        if self.max_margin<self.min_margin or self.take_pct<self.stop_pct*Decimal('1.5') or self.portfolio_risk_pct<self.risk_pct:
            raise ValueError('Проверь маржу, отношение стопа/тейка и суммарный риск.')
        return self

class Quote(Strict):
    signal_id:str=Field(min_length=1,max_length=64)
    contract_id:int=Field(ge=0,strict=True)
    side:Literal['long','short']
    margin:Annotated[Decimal|None,Field(gt=0,le=10000,allow_inf_nan=False)]=None

class Execute(Strict):
    preview_hash:str=Field(min_length=64,max_length=64)
    confirm:Literal['OPEN_REAL_POSITION']

class Setup(Strict):confirm:Literal['PREPARE_PERPS_ACCOUNT']
class ResolveAbsent(Strict):confirm:Literal['CONFIRM_NO_POSITION_OR_ORDER']


def register_walt_trade_routes(app,path,require_editor):
    agent=WaltAgent(path);service=WaltTrading(agent)
    router=APIRouter(prefix='/api/walt-trading')

    async def call(fn,*args):
        try:return await run_in_threadpool(fn,*args)
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc

    @router.get('/overview')
    def overview():return service.overview()

    @router.get('/scan-status')
    def scan_status():return service.scan_progress()

    @router.post('/wallet-refresh')
    async def wallet_refresh(request:Request):
        require_editor(request);return await call(service.sync_wallet)

    @router.put('/limits')
    async def limits(payload:Limits,request:Request):
        require_editor(request);data=payload.model_dump(mode='json');revision=data.pop('revision')
        return await call(service.save_limits,data,revision)

    @router.post('/signal')
    async def signal(request:Request):
        require_editor(request);return await call(service.signal)

    @router.post('/quote')
    async def quote(payload:Quote,request:Request):
        require_editor(request);return await call(service.quote,payload.signal_id,payload.contract_id,payload.side,payload.margin)

    @router.post('/auth')
    async def auth(request:Request):
        require_editor(request);return await call(agent.start_auth,str(request.base_url),True)

    @router.post('/account-setup')
    async def setup(payload:Setup,request:Request):
        require_editor(request)
        def check():
            from .walt_trading import unpack
            sid=agent.connect()
            return unpack(agent.rpc('tools/call',{'name':'perps_readiness','arguments':{}},sid,_manual=True)[0])
        return await call(check)

    @router.post('/drafts/{ident}/prepare')
    async def prepare(ident:str,request:Request):
        require_editor(request);return await call(service.prepare,ident)

    @router.post('/drafts/{ident}/execute')
    async def execute(ident:str,payload:Execute,request:Request):
        require_editor(request);return await call(service.execute,ident,payload.preview_hash,payload.confirm)

    @router.post('/drafts/{ident}/reconcile')
    async def reconcile(ident:str,request:Request):
        require_editor(request);return await call(service.reconcile,ident)

    @router.post('/drafts/{ident}/resolve-absent')
    async def resolve_absent(ident:str,payload:ResolveAbsent,request:Request):
        require_editor(request);return await call(service.resolve_absent,ident,payload.confirm)

    app.include_router(router)
