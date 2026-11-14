"""Validated, CSRF-protected manual portfolio API."""
from datetime import date
from decimal import Decimal
import os
import re
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .monthly_investments import fetch_cbr_usd
from .walt_portfolio import Conflict, WaltPortfolio, now

Number = Annotated[Decimal, Field(ge=0, le=Decimal('1000000000000'), max_digits=30, decimal_places=18, allow_inf_nan=False)]


class Revision(BaseModel):
    revision: int = Field(ge=0, strict=True)


class Position(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=150)
    kind: Literal['crypto', 'stablecoin', 'tokenized_stock', 'tokenized_etf', 'other']
    quantity: Number
    unit_price_usd: Number | None = None
    cost_usd: Number | None = None
    valued_on: date
    notes: str = Field(default='', max_length=1500)

    @field_validator('symbol', 'name', 'notes')
    @classmethod
    def strip_text(cls, value, info):
        value = value.strip()
        if info.field_name != 'notes' and not value:
            raise ValueError('Поле не должно быть пустым')
        if info.field_name == 'symbol' and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]*', value):
            raise ValueError('Укажи точный тикер латиницей, например BTC или SPYx')
        return value

    @field_validator('quantity')
    @classmethod
    def positive_quantity(cls, value):
        if value <= 0:
            raise ValueError('Количество должно быть больше нуля; закрытую позицию удали')
        return value

    @field_validator('valued_on')
    @classmethod
    def past_date(cls, value):
        if value > now().date():
            raise ValueError('Дата оценки не может быть в будущем')
        return value


class PositionUpdate(Position, Revision):
    pass


class Fx(Revision):
    rate: Annotated[Decimal, Field(gt=0, le=1000000, max_digits=18, decimal_places=10, allow_inf_nan=False)]
    as_of: date

    @field_validator('as_of')
    @classmethod
    def past_date(cls, value):
        return Position.past_date(value)


def register_walt_routes(app, db_path, require_editor):
    store = WaltPortfolio(db_path)
    router = APIRouter(prefix='/api/assets')

    def accounts():
        return tuple(x.strip() for x in os.getenv('TINVEST_ACCOUNT_IDS', '').split(',') if x.strip())

    @router.get('/overview')
    def overview():
        return store.summary(accounts())

    @router.get('/history')
    def history():
        return store.history()

    @router.post('/walt', status_code=201)
    def create(payload: Position, request: Request):
        require_editor(request)
        try:
            return store.save(payload.model_dump(), account_ids=accounts())
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.put('/walt/{position_id}')
    def update(position_id: int, payload: PositionUpdate, request: Request):
        require_editor(request)
        try:
            return store.save(payload.model_dump(exclude={'revision'}), position_id, payload.revision, accounts())
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.delete('/walt/{position_id}', status_code=204)
    def delete(position_id: int, payload: Revision, request: Request):
        require_editor(request)
        try:
            store.delete(position_id, payload.revision, accounts())
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.put('/fx')
    def fx(payload: Fx, request: Request):
        require_editor(request)
        try:
            return store.set_fx(payload.rate, payload.as_of, 'manual', payload.revision, accounts())
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post('/fx/cbr')
    def cbr(payload: Revision, request: Request):
        require_editor(request)
        try:
            result = fetch_cbr_usd()
            checked = Fx(rate=result['rate'], as_of=result['date'], revision=payload.revision)
        except Exception as exc:
            raise HTTPException(503, 'Курс ЦБ недоступен. Сохранённый курс не изменён.') from exc
        try:
            return store.set_fx(checked.rate, checked.as_of, 'cbr', checked.revision, accounts())
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post('/snapshot', status_code=201)
    def snapshot(request: Request):
        require_editor(request)
        store.capture(accounts())
        return {'status': 'saved'}

    app.include_router(router)
