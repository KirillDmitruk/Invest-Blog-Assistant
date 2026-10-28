"""Documented T-Invest REST /rest endpoints. Read-only by construction."""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

import requests

from .model import Instrument, iso_dt, utc_bounds

log = logging.getLogger(__name__)
BASE = "https://invest-public-api.tbank.ru/rest/tinkoff.public.invest.api.contract.v1."


class TInvestError(RuntimeError):
    pass


class TInvest:
    def __init__(self, token: str, tz: ZoneInfo, session: requests.Session | None = None):
        self.tz = tz
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        self.cache: dict[str, Instrument] = {}
        self._last_request = 0.0

    def call(self, service: str, method: str, payload: dict | None = None) -> dict:
        url = BASE + service + "/" + method
        for attempt in range(4):
            # Respect modest request pacing across sequential calls.
            delay = .25 - (time.monotonic() - self._last_request)
            if delay > 0:
                time.sleep(delay)
            self._last_request = time.monotonic()
            try:
                response = self.session.post(url, json=payload or {}, timeout=25)
            except requests.RequestException as exc:
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise TInvestError(f"Сетевая ошибка T-Invest {method}: {exc.__class__.__name__}") from exc
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            if not response.ok:
                raise TInvestError(f"T-Invest {method}: HTTP {response.status_code}: {response.text[:250]}")
            try:
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("Unexpected JSON shape")
                return data
            except ValueError as exc:
                raise TInvestError(f"Некорректный JSON от T-Invest {method}") from exc
        raise TInvestError(f"Превышено число попыток T-Invest {method}")

    def accounts(self) -> list[dict]:
        return self.call("UsersService", "GetAccounts", {"status": "ACCOUNT_STATUS_OPEN"}).get("accounts", [])

    def portfolio(self, account_id: str) -> dict:
        return self.call("OperationsService", "GetPortfolio", {"accountId": account_id, "currency": "RUB"})

    def operations(self, account_id: str, start: date, end: date) -> list[dict]:
        if start > end:
            return []
        from_utc, to_utc = utc_bounds(start, end, self.tz)
        payload = {"accountId": account_id, "from": from_utc, "to": to_utc,
                   "limit": 500, "state": "OPERATION_STATE_EXECUTED"}
        result, seen, cursors = [], set(), set()
        for _ in range(100):
            page = self.call("OperationsService", "GetOperationsByCursor", payload)
            items = page.get("items", [])
            if not isinstance(items, list):
                raise TInvestError("GetOperationsByCursor вернул ответ без массива items")
            log.info("T-Invest: счёт #%s, страница операций: %s элементов", account_id[-4:], len(items))
            for item in items:
                if not item.get("type") and not item.get("operationType"):
                    raise TInvestError("В ответе GetOperationsByCursor нет поля type: формат API изменился")
                if item.get("state") not in (None, "OPERATION_STATE_EXECUTED", 1):
                    continue
                ts = item.get("date")
                if not ts:
                    continue
                local_day = iso_dt(ts).astimezone(self.tz).date()
                if not start <= local_day <= end:
                    continue
                # 'id' is expected for broker operations, fallback cursor if absent.
                key = item.get("id") or item.get("cursor") or f"{ts}:{(item.get('type') or item.get('operationType'))}:{item.get('payment')}"
                if key not in seen:
                    result.append(item)
                    seen.add(key)
            if not page.get("hasNext"):
                return sorted(result, key=lambda x: x["date"], reverse=True)
            cursor = page.get("nextCursor")
            if not cursor or cursor in cursors:
                raise TInvestError("Некорректная пагинация GetOperationsByCursor")
            cursors.add(cursor)
            payload["cursor"] = cursor
        raise TInvestError("Слишком много страниц операций (>100). Уменьши период.")

    def instrument(self, uid: str = "", figi: str = "") -> Instrument:
        identifier = uid or figi
        if not identifier:
            raise TInvestError("Нет UID/FIGI инструмента")
        if identifier in self.cache:
            return self.cache[identifier]
        id_type = "INSTRUMENT_ID_TYPE_UID" if uid else "INSTRUMENT_ID_TYPE_FIGI"
        raw = self.call("InstrumentsService", "GetInstrumentBy", {"idType": id_type, "id": identifier}).get("instrument", {})
        if not raw:
            raise TInvestError(f"Не найден инструмент {identifier}")
        info = Instrument(
            uid=raw.get("uid", uid), figi=raw.get("figi", figi),
            name=raw.get("name", "Неизвестный инструмент"), isin=raw.get("isin", ""),
            ticker=raw.get("ticker", ""), kind=raw.get("instrumentType", "").lower(),
            currency=raw.get("currency", "RUB").upper(), raw=raw,
        )
        for key in (info.uid, info.figi):
            if key:
                self.cache[key] = info
        return info

    def bond(self, uid: str) -> dict:
        return self.call("InstrumentsService", "BondBy", {"idType": "INSTRUMENT_ID_TYPE_UID", "id": uid}).get("instrument", {})

    def coupons(self, uid: str, start: date, end: date) -> list[dict]:
        a, b = utc_bounds(start, end, self.tz)
        return self.call("InstrumentsService", "GetBondCoupons", {"instrumentId": uid, "from": a, "to": b}).get("events", [])

    def dividends(self, uid: str, start: date, end: date) -> list[dict]:
        a, b = utc_bounds(start, end, self.tz)
        return self.call("InstrumentsService", "GetDividends", {"instrumentId": uid, "from": a, "to": b}).get("dividends", [])
