from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from .model import Draft, fmt_money, fmt_num, fmt_totals, group_amounts, iso_day, money, quotation
from .tinvest import TInvest, TInvestError

COUPON = {"OPERATION_TYPE_COUPON"}
DIVIDEND = {"OPERATION_TYPE_DIVIDEND", "OPERATION_TYPE_DIV_EXT"}
REPAYMENT = {"OPERATION_TYPE_BOND_REPAYMENT", "OPERATION_TYPE_BOND_REPAYMENT_FULL"}
DEPOSIT = {"OPERATION_TYPE_INPUT", "OPERATION_TYPE_INPUT_ACQUIRING",
           "OPERATION_TYPE_INPUT_SWIFT", "OPERATION_TYPE_INP_MULTI"}
BUY = {"OPERATION_TYPE_BUY", "OPERATION_TYPE_BUY_CARD", "OPERATION_TYPE_BUY_MARGIN"}

def operation_type(operation: dict) -> str:
    """GetOperationsByCursor returns `type` (OperationItem), not `operationType`.

    The old field is accepted only for compatibility with imported/test data.
    """
    return operation.get("type") or operation.get("operationType") or ""


# Curated glossary lessons are maintained separately from portfolio calculations.
from .lessons import TERMS


def previous_workweek(today: date) -> tuple[date, date]:
    monday = today - timedelta(days=today.weekday())
    if today.weekday() < 5:
        monday -= timedelta(days=7)
    return monday, monday + timedelta(days=4)


def previous_month(today: date) -> tuple[date, date]:
    end = today.replace(day=1) - timedelta(days=1)
    return end.replace(day=1), end


def current_week(today: date) -> tuple[date, date]:
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=6)


def d(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def is_event_in_window(day: date | None, first: date, last: date) -> bool:
    return day is not None and first <= day <= last


class PostGenerator:
    def __init__(self, api: TInvest, accounts: tuple[str, ...], events_path: Path,
                 recent_papers: tuple[str, ...] = (), recent_glossary: tuple[str, ...] = ()):
        if not accounts:
            raise ValueError("Не заданы ID счетов")
        self.api = api
        self.accounts = accounts
        self.events_path = events_path
        self.recent_papers = recent_papers
        self.recent_glossary = recent_glossary
        self.selected_glossary_topic: str | None = None
        self.selected_paper_uid: str | None = None

    def generate(self, kind: str, today: date) -> Draft:
        handlers = {"weekly": self.weekly, "monthly": self.monthly, "plan": self.plan,
                    "paper": self.paper, "glossary": self.glossary, "purchase": self.purchase}
        if kind not in handlers:
            raise ValueError(f"Неизвестная рубрика {kind}")
        return handlers[kind](today)

    def get_operations(self, start: date, end: date) -> list[dict]:
        result = []
        for account in self.accounts:
            ops = self.api.operations(account, start, end)
            result.extend(ops)
        return sorted(result, key=lambda x: x["date"], reverse=True)

    def get_positions(self) -> tuple[list[dict], Decimal]:
        positions: list[dict] = []
        total = Decimal(0)
        for account in self.accounts:
            portfolio = self.api.portfolio(account)
            value, currency = money(portfolio.get("totalAmountPortfolio"))
            if currency != "RUB":
                raise ValueError("Ожидалась валюта портфеля RUB")
            total += value
            for position in portfolio.get("positions", []):
                if position.get("instrumentType") in ("bond", "share") and quotation(position.get("quantity")) > 0:
                    positions.append(position)
        return positions, total

    def describe_op(self, op: dict) -> str:
        ident = op.get("instrumentUid", "")
        figi = op.get("figi", "")
        try:
            instrument = self.api.instrument(uid=ident, figi=figi)
            return instrument.name + instrument.tag
        except TInvestError:
            name = op.get("name") or op.get("description") or "Бумага"
            return f"{name} (FIGI {figi})" if figi else name

    def render_ledger(self, start: date, end: date, monthly: bool = False) -> str:
        ops = self.get_operations(start, end)
        pools = {
            "Купоны": [o for o in ops if operation_type(o) in COUPON],
            "Дивиденды": [o for o in ops if operation_type(o) in DIVIDEND],
            "Погашения": [o for o in ops if operation_type(o) in REPAYMENT],
            "Пополнения": [o for o in ops if operation_type(o) in DEPOSIT],
        }
        purchases = [o for o in ops if operation_type(o) in BUY] if monthly else []
        head = f"📈 Итоги за {d(start)}–{d(end)}" if monthly else "Делаю отчет за неделю🫡"
        sections = [head, "", "Были следующие выплаты:"]
        icons = {"Купоны": "💰", "Дивиденды": "🏦", "Погашения": "💸", "Пополнения": "➕"}
        for title, group in pools.items():
            if title in ("Дивиденды", "Погашения") and not group:
                continue
            sections += ["", f"{icons[title]} {title}"]
            if not group:
                sections.append("Выплат не было" if title != "Пополнения" else "Пополнений не было")
            elif title == "Пополнения":
                sections.extend(fmt_money(*money(item.get("payment"))) for item in group)
            else:
                for item in group:
                    amount, currency = money(item.get("payment"))
                    sections.append(f"{self.describe_op(item)} — {fmt_money(amount, currency)}")
        sections.extend(["", "Итог:"])
        for title, group in pools.items():
            sections.append(f"{title} — {fmt_totals(group_amounts(group))}")
        if monthly:
            # A monthly activity summary; no purchase list (the weekly format stays unchanged).
            purchase_sums = group_amounts(purchases)
            spent = {cur: abs(value) for cur, value in purchase_sums.items()}
            sections.extend(["", "🛒 Покупки за месяц",
                             f"Количество операций — {len(purchases)}",
                             f"Сумма списаний — {fmt_totals(spent)}" if purchases else "Покупок не было"])
            # Snapshot is CURRENT (not the historical last-day valuation).
            positions, current = self.get_positions()
            sections.extend(["", f"💼 Стоимость портфеля на сегодня: {fmt_money(current, 'RUB')}",
                             f"Активных позиций (облигации и акции): {len(positions)}",
                             "", "Стоимость портфеля — текущий снимок, не доходность за месяц."])
        sections.extend(["", "Суммы по исполненным операциям брокера; налоги и комиссии отдельно не вычитались."])
        return "\n".join(sections)

    def weekly(self, today: date) -> Draft:
        start, end = previous_workweek(today)
        return Draft("weekly", f"{start.isoformat()}_{end.isoformat()}", self.render_ledger(start, end))

    def monthly(self, today: date) -> Draft:
        start, end = previous_month(today)
        return Draft("monthly", start.strftime("%Y-%m"), self.render_ledger(start, end, monthly=True))

    def events(self, start: date, end: date) -> tuple[list[str], bool]:
        if not self.events_path.exists():
            return [], False
        records = json.loads(self.events_path.read_text(encoding="utf-8"))
        matches = [f"• {d(day)} — {event['title']} ({event.get('source', 'источник не указан')})"
                   for event in records if start <= (day := date.fromisoformat(event["date"])) <= end]
        return matches, True

    def plan(self, today: date) -> Draft:
        start, end = current_week(today)
        positions, _ = self.get_positions()
        coupons, dividends, cutoffs, errors = [], [], [], []
        for position in positions:
            uid = position.get("instrumentUid") or ""
            figi = position.get("figi") or ""
            if not uid and not figi:
                continue
            try:
                instrument = self.api.instrument(uid=uid, figi=figi)
                quantity = quotation(position.get("quantity"))
                if instrument.kind == "bond":
                    for event in self.api.coupons(instrument.uid or figi, start, end):
                        day = iso_day(event.get("couponDate"), self.api.tz)
                        if not is_event_in_window(day, start, end):
                            continue
                        amount, currency = money(event.get("payOneBond"))
                        estimate = fmt_money(amount * quantity, currency) if amount else "размер уточняется"
                        coupons.append((day, f"• {d(day)} — {instrument.name}{instrument.tag}: ~{estimate} на текущий объём {fmt_num(quantity, 0)} шт."))
                elif instrument.kind == "share":
                    # Dividends method filters by RECORD date, not PAYMENT date.
                    # Fetch lookback to capture payments dated this week after a past ex-date.
                    for event in self.api.dividends(instrument.uid or figi, start - timedelta(days=370), end + timedelta(days=30)):
                        if "cancel" in event.get("dividendType", "").lower():
                            continue
                        amount, currency = money(event.get("dividendNet"))
                        estimate = fmt_money(amount * quantity, currency) if amount else "размер уточняется"
                        pay_day = iso_day(event.get("paymentDate"), self.api.tz)
                        if is_event_in_window(pay_day, start, end):
                            dividends.append((pay_day, f"• {d(pay_day)} — {instrument.name}{instrument.tag}: ~{estimate} по текущему количеству"))
                        buy_day = iso_day(event.get("lastBuyDate"), self.api.tz)
                        if is_event_in_window(buy_day, start, end):
                            cutoffs.append((buy_day, f"• {d(buy_day)} — {instrument.name}{instrument.tag}: последний день покупки для дивиденда"))
                        record_day = iso_day(event.get("recordDate"), self.api.tz)
                        if is_event_in_window(record_day, start, end):
                            cutoffs.append((record_day, f"• {d(record_day)} — {instrument.name}{instrument.tag}: дата закрытия реестра"))
            except (TInvestError, ValueError) as exc:
                errors.append(f"{uid or figi}: {str(exc)[:90]}")
        cb, has_calendar = self.events(start, end)
        parts = [f"📅 План на неделю: {d(start)}–{d(end)}", "",
                 "💰 Ожидаемые купоны", *(x[1] for x in sorted(coupons)),
                 *([] if coupons else ["По доступному календарю событий не найдено."]),
                 "", "🏦 Дивиденды: ориентировочные даты выплат", *(x[1] for x in sorted(dividends)),
                 *([] if dividends else ["По доступному календарю событий не найдено."]),
                 "", "📌 Дивидендные отсечки / последний день покупки", *(x[1] for x in sorted(cutoffs)),
                 *([] if cutoffs else ["По доступному календарю событий не найдено."]),
                 "", "🏛 События Банка России", *(cb if cb else ["В локальном списке событий на эту неделю записей нет." if has_calendar else "Список событий ЦБ не подключён."]),
                 "", "🎯 Мои планы на неделю: [добавить вручную]",
                 "", "⚠️ Календарь предварительный: даты и суммы могут меняться; оценка сумм основана на текущем числе бумаг и не гарантирует поступление. При изменении позиций суммы пересчитаются."]
        if errors:
            parts += ["", "⚠️ Данные по части бумаг получить не удалось — проверь вручную:", *errors]
        return Draft("plan", start.isoformat(), "\n".join(parts))

    def paper(self, today: date) -> Draft:
        """Editorial template: factual T-Invest fields plus visible manual research prompts.

        T-Invest does not supply an issuer's narrative, audited financial analysis,
        credit-agency rating or a trustworthy outlook. Never fabricate those fields.
        Bond ``currentPrice`` may be quoted as a percentage of face value, so it
        must not be multiplied by quantity to claim the position's value.
        """
        positions, total = self.get_positions()
        monday, _ = current_week(today)
        ordered = sorted(positions, key=lambda x: (x.get("instrumentUid") or x.get("figi") or ""))
        if not ordered:
            raise ValueError("Нет акций/облигаций в портфеле для «Бумаги недели»")

        recent_buys: set[str] = set()
        try:
            for op in self.get_operations(today - timedelta(days=45), today):
                if operation_type(op) in BUY:
                    recent_buys.add(op.get("instrumentUid") or op.get("figi") or "")
        except TInvestError:
            pass

        def score(pos):
            uid = pos.get("instrumentUid") or pos.get("figi") or ""
            recent_index = self.recent_papers.index(uid) if uid in self.recent_papers else None
            # Selection signal only. The live bond quotation may be in points,
            # and must NOT be presented as a valid ruble valuation.
            quantity = quotation(pos.get("quantity"))
            price, currency = money(pos.get("currentPrice"))
            native_value = quantity * price if (pos.get("instrumentType") == "share" and currency == "RUB") else Decimal(0)
            return (1 if recent_index is None else 0,
                    1 if uid in recent_buys else 0,
                    native_value, recent_index if recent_index is not None else 0, uid)

        pos = max(ordered, key=score)
        self.selected_paper_uid = pos.get("instrumentUid") or pos.get("figi")
        info = self.api.instrument(uid=pos.get("instrumentUid", ""), figi=pos.get("figi", ""))
        quantity = quotation(pos.get("quantity"))
        is_bond = info.kind == "bond"
        position_type = "облигация" if is_bond else "акция" if info.kind == "share" else info.kind
        hooks = (
            "Интересная бумага — это не только цифры на экране. Давайте посмотрим, что за ними стоит.",
            "В этом выпуске разбираем не только условия бумаги, но и сам бизнес, его сильные стороны и ограничения.",
            "Попробуем понять, из чего складываются привлекательность бумаги и её главные риски.",
        )
        parts = [
            f"📊 Бумага недели — {info.name}{info.tag}", "",
            hooks[today.isocalendar().week % len(hooks)], "",
            "🏢 Что за компания?",
            "[Чем занимается эмитент, какие товары или услуги предлагает, на чём зарабатывает? "
            "Укажи 2–4 предложения и место компании на рынке — только по проверенным источникам.]", "",
            "📈 Что происходит с компанией сейчас?",
            "[Последняя доступная отчётность: выручка, прибыль, долговая нагрузка и период сравнения. "
            "Добавь 2–3 актуальных события из бизнеса/отрасли и источники с датами. "
            "Если свежих данных нет — напиши об этом прямо.]", "",
            "💰 Что по самой бумаге?",
            f"Тип: {position_type}",
            f"В моём портфеле: {fmt_num(quantity, 0)} шт.",
        ]
        if is_bond:
            bond = self.api.bond(info.uid)
            maturity = iso_day(bond.get("maturityDate"), self.api.tz)
            floating = bond.get("floatingCouponFlag")
            amortization = bond.get("amortizationFlag")
            parts.extend([
                f"Дата погашения: {d(maturity) if maturity else '[уточнить условия выпуска]'}",
                "Плавающий купон: " + ("да" if floating is True else "нет" if floating is False else "[уточнить]"),
                "Амортизация: " + ("да" if amortization is True else "нет" if amortization is False else "[уточнить]"),
            ])
            nominal, nominal_currency = money(bond.get("nominal"))
            if nominal > 0 and nominal_currency == "RUB":
                parts.append(f"Текущий номинал: {fmt_money(nominal, nominal_currency)}")
            else:
                parts.append("Текущий номинал: [сверить условия выпуска, особенно амортизацию]")
            parts.extend([
                "Купонная ставка / формула: [указать актуальную ставку, частоту и дату проверки]",
                "Доходность к погашению: [проверить на дату публикации; учесть НКД/оферту]",
                "Кредитный рейтинг: [агентство, рейтинг, дата последнего действия]",
            ])
        elif info.kind == "share":
            price, currency = money(pos.get("currentPrice"))
            if price > 0:
                parts.append(f"Текущая цена (снимок API): {fmt_money(price, currency)}")
                value = price * quantity
                if currency == "RUB" and total > 0:
                    parts.append(f"Примерная доля портфеля: {fmt_num(value / total * 100)}%")
            parts.extend([
                "Дивиденды: [последние объявленные выплаты, период и доходность при актуальной цене]",
                "Дивидендная политика / отсечка: [дата и источник; если не объявлено — так и написать]",
                "Финансовые показатели: [выручка, прибыль, долг, отчётный период]",
            ])
        parts += [
            "", "🚀 Какие перспективы?",
            "[На горизонте 1–3 лет: что может поддержать результаты компании или привлекательность выпуска? "
            "Опиши базовый и благоприятный сценарии, указав предпосылки, а не обещания роста.]",
            "", "✅ Что мне нравится",
            "— [Преимущество №1: чем подтверждается и почему важно]",
            "— [Преимущество №2: чем подтверждается и почему важно]",
            "", "⚠️ Основные риски",
            "— [Риск №1: что может пойти не так и как это отразится на бумаге]",
            "— [Риск №2: что может пойти не так и как это отразится на бумаге]",
            "", "🤔 Моё мнение",
            "[Почему эта бумага в портфеле? Подходит ли она моей долгосрочной стратегии? "
            "Что заставит пересмотреть решение? Заполни лично — генератор не знает твоих мотивов.]",
            "", "📍 Итог",
            "[2–3 предложения: для какого инвестора бумага может быть интересна, при каких условиях "
            "и какой главный риск нельзя игнорировать.]",
            "", "А у вас есть такая бумага? Что думаете о компании? 👇",
            "", "Данные и котировки требуют проверки на дату публикации. Это личный разбор, не индивидуальная инвестиционная рекомендация.",
        ]
        return Draft("paper", monday.isoformat(), "\n".join(parts))

    def glossary(self, today: date) -> Draft:
        monday, _ = current_week(today)
        # Prefer a term that has not appeared in the recent editorial history.
        offset = (today.isocalendar().year * 53 + today.isocalendar().week - 1) % len(TERMS)
        rotated = TERMS[offset:] + TERMS[:offset]
        title, body = next(((t, b) for t, b in rotated if t not in self.recent_glossary[:6]), rotated[0])
        self.selected_glossary_topic = title
        return Draft(
            "glossary", monday.isoformat(),
            f"📚 Ликбез за 2 минуты: {title}\n\n{body}\n\n"
            "Вот и всё — ещё одним сложным инвестиционным термином меньше 😁\n"
            "О каком понятии рассказать в следующий раз? 👇"
        )

    def purchase(self, today: date) -> Draft:
        start = today - timedelta(days=60)
        buys = [o for o in self.get_operations(start, today) if operation_type(o) in BUY]
        if not buys:
            return Draft("purchase", today.isoformat(), "\n".join([
                "Сделал покупку🛒", "", "[название] $[ТИКЕР или ISIN]",
                "Количество: [шт.] по [цена]", "Сумма: [сумма]",
                "", "Для облигации: купон [X%], доходность к погашению [X%], погашение [дата], рейтинг [рейтинг]",
                "Для акции: дивиденд [X ₽, доходность X%], ближайшая отсечка [дата]",
                "", "Почему купил: [1–2 предложения]", "Доля в портфеле после покупки: [X%]",
                "Что дальше: [планы по сектору]", "", "За последние 60 дней покупок в API не найдено. Заполни вручную."
            ]))
        op = buys[0]
        info = self.api.instrument(uid=op.get("instrumentUid", ""), figi=op.get("figi", ""))
        quantity_done = Decimal(str(op.get("quantityDone") or "0"))
        quantity = quantity_done if quantity_done > 0 else Decimal(str(op.get("quantity") or "0"))
        price, currency = money(op.get("price"))
        total, total_cur = money(op.get("payment"))
        parts = ["Сделал покупку🛒", "", f"{info.name}{info.tag}",
                 f"Количество: {fmt_num(quantity, 0)} шт. по {fmt_money(price, currency)}",
                 f"Сумма списания: {fmt_money(abs(total), total_cur)}",
                 f"Дата операции: {d(iso_day(op.get('date'), self.api.tz))}"]
        if info.kind == "bond":
            bond = self.api.bond(info.uid)
            maturity = iso_day(bond.get("maturityDate"), self.api.tz)
            parts.extend(["Купонная ставка: [проверить ставку и тип купона]",
                          "Доходность к погашению: [рассчитать на дату покупки]",
                          f"Погашение: {d(maturity) if maturity else '[не указано]'}",
                          "Кредитный рейтинг: [внешний источник]"])
        elif info.kind == "share":
            parts += ["Дивиденд: [подтверждённая сумма, доходность]", "Ближайшая отсечка: [проверить]"]
        parts += ["", "Почему купил: [1–2 предложения]",
                  "Доля в портфеле после покупки: [проверить текущую долю, не историческую]",
                  "Что дальше: [планы по сектору]",
                  "", "Проверь сумму сделки и комиссии в приложении перед публикацией."]
        return Draft("purchase", op.get("id") or op.get("date", today.isoformat()), "\n".join(parts))
