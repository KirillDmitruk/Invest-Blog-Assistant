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


# Curated, deterministic educational posts; no hallucinated ratings/yields.
TERMS = [
    ("Доходность к погашению (YTM)",
     "Это расчетная годовая доходность облигации, если купить её по текущей цене и держать до погашения с учетом будущих платежей. Она зависит и от купонов, и от разницы между ценой покупки и возвратом номинала.\n\nПример: облигация дешевле номинала может иметь доходность к погашению выше купонной ставки.\n\nВажно: расчет предполагает получение обещанных платежей, а метод расчёта может учитывать реинвестирование. YTM не гарантирует прибыль и не учитывает все индивидуальные налоги/комиссии."),
    ("Дюрация", "Дюрация показывает, насколько во времени распределены денежные потоки облигации. Модифицированная дюрация приблизительно измеряет чувствительность цены к изменению доходности.\n\nПример: при модифицированной дюрации 4 рост рыночной доходности на 1 процентный пункт может вызвать снижение цены примерно на 4% — при прочих равных.\n\nВажно: это приближение, а не гарантия. Дюрация может изменяться со временем."),
    ("Флоатер", "Флоатер — облигация с плавающим купоном. Его ставка привязана к ориентиру, например ключевой ставке или RUONIA, плюс/минус спред.\n\nПлюс: купон может повышаться при росте базовой ставки. Минус: при снижении ставки купон тоже может уменьшиться.\n\nВажно: плавающий купон не отменяет кредитный риск и риск ликвидности."),
    ("ИИС", "Индивидуальный инвестиционный счёт — специальный брокерский счёт с предусмотренными законом налоговыми льготами при соблюдении условий.\n\nСуществуют разные поколения ИИС, поэтому сроки владения и правила льгот зависят от даты открытия и вида счёта.\n\nВывод: перед открытием уточни актуальные условия именно своего ИИС и применимость льгот."),
    ("Налоговый вычет", "Налоговый вычет может уменьшать налогооблагаемую базу или давать право на возврат части уже уплаченного НДФЛ при выполнении законных условий.\n\nВ инвестициях вычеты могут зависеть от типа счёта, даты его открытия, суммы взносов и других обстоятельств.\n\nВажно: лимиты и требования меняются. Проверять их лучше по актуальным правилам ФНС."),
    ("НКД", "НКД — накопленный купонный доход. Между выплатами купон постепенно накапливается и обычно учитывается в цене сделки с облигацией отдельно от «чистой» котировки.\n\nЕсли покупаешь бумагу перед купоном, как правило, оплачиваешь продавцу НКД. Поэтому ближайшая купонная выплата сама по себе не означает мгновенную прибыль."),
    ("Амортизация облигации", "При амортизации эмитент возвращает номинал не один раз в конце срока, а частями по графику.\n\nПлюс: часть денег возвращается раньше. Минус: оставшаяся база для будущих купонов может уменьшаться, а возвращенные деньги нужно размещать заново.\n\nВажно смотреть полный график выплат, а не только дату финального погашения."),
    ("Кредитный рейтинг", "Кредитный рейтинг — оценка способности эмитента исполнять долговые обязательства. Его присваивают рейтинговые агентства.\n\nБолее высокий рейтинг обычно означает меньший оцениваемый кредитный риск, но не гарантирует выплаты. Рейтинг может быть пересмотрен.\n\nВажно: оценка «уровня риска» у брокера — не то же самое, что кредитный рейтинг агентства."),
    ("Дивидендная отсечка", "Дата закрытия реестра определяет круг акционеров с правом на дивиденд. Из-за режима расчётов последний день покупки для попадания в реестр может быть раньше.\n\nПосле отсечки цена акции часто корректируется, а деньги приходят позже. Покупка исключительно ради дивидендов не гарантирует положительную доходность."),
    ("Диверсификация", "Диверсификация — распределение вложений между разными эмитентами, секторами, видами активов и валютами.\n\nЗачем: неприятности одной компании могут меньше повлиять на весь портфель.\n\nВажно: диверсификация не устраняет рыночный риск; активы иногда падают одновременно."),
    ("Оферта по облигации", "Оферта — предусмотренный условиями выпуска механизм, по которому у инвестора или эмитента может возникать право/обязанность досрочного выкупа бумаги.\n\nЦена и порядок зависят от выпуска. Не путай оферту с безусловным погашением. Перед покупкой стоит прочитать эмиссионные документы."),
    ("Купон и доходность", "Купон — выплаты по облигации, а доходность — показатель результата инвестиции с учётом цены и иных денежных потоков.\n\nПример: облигация с купоном 12% годовых, купленная дороже номинала, может иметь доходность к погашению ниже 12%.\n\nПоэтому одного размера купона недостаточно, чтобы сравнивать облигации."),
]


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
                 recent_papers: tuple[str, ...] = ()):
        if not accounts:
            raise ValueError("Не заданы ID счетов")
        self.api = api
        self.accounts = accounts
        self.events_path = events_path
        self.recent_papers = recent_papers
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
        positions, total = self.get_positions()
        monday, _ = current_week(today)
        ordered = sorted(positions, key=lambda x: (x.get("instrumentUid") or x.get("figi") or ""))
        if not ordered:
            raise ValueError("Нет акций/облигаций в портфеле для «Бумаги недели»")
        # Prefer recently purchased, relatively large positions not covered recently.
        # New purchase alone is NOT a recommendation to buy again.
        recent_buys: set[str] = set()
        try:
            for op in self.get_operations(today - timedelta(days=45), today):
                if operation_type(op) in BUY:
                    recent_buys.add(op.get("instrumentUid") or op.get("figi") or "")
        except TInvestError:
            pass  # no selection from unverified purchases; holdings still available
        def score(pos):
            uid = pos.get("instrumentUid") or pos.get("figi") or ""
            recent_index = self.recent_papers.index(uid) if uid in self.recent_papers else None
            quantity = quotation(pos.get("quantity"))
            price, currency = money(pos.get("currentPrice"))
            native_value = quantity * price if currency == "RUB" else Decimal(0)
            return (1 if recent_index is None else 0,
                    1 if uid in recent_buys else 0,
                    native_value, recent_index if recent_index is not None else 0, uid)
        pos = max(ordered, key=score)
        self.selected_paper_uid = pos.get("instrumentUid") or pos.get("figi")
        info = self.api.instrument(uid=pos.get("instrumentUid", ""), figi=pos.get("figi", ""))
        quantity = quotation(pos.get("quantity"))
        price, currency = money(pos.get("currentPrice"))
        amount = price * quantity
        share = (amount / total * 100) if total > 0 and currency == "RUB" else None
        parts = ["📊 Бумага недели", "", f"{info.name}{info.tag}",
                 f"Тип: {'облигация' if info.kind == 'bond' else 'акция' if info.kind == 'share' else info.kind}",
                 f"В моём портфеле: {fmt_num(quantity, 0)} шт.",
                 f"Текущая оценка позиции: {fmt_money(amount, currency)}"]
        if share is not None:
            parts.append(f"Примерная доля от стоимости портфеля: {fmt_num(share)}%")
        if info.kind == "bond":
            bond = self.api.bond(info.uid)
            m = iso_day(bond.get("maturityDate"), self.api.tz)
            parts.extend([f"Погашение: {d(m) if m else 'не указано / бессрочная'}",
                          f"Купон: {'плавающий' if bond.get('floatingCouponFlag') else 'не помечен как плавающий'}",
                          f"Амортизация: {'да' if bond.get('amortizationFlag') else 'нет по данным API'}",
                          "Кредитный рейтинг: [проверить в агентстве]",
                          "Доходность к погашению: [указать после проверки котировки и расчёта]"])
        elif info.kind == "share":
            parts += ["Дивиденды: [посмотреть последнее объявление и дату отсечки]"]
        parts += ["", "✅ Что мне нравится: [добавить 1–2 тезиса]",
                  "⚠️ Основные риски: [добавить 1–2 тезиса]",
                  "🤔 Почему держу / почему нет: [твоё мнение]",
                  "📍 План по бумаге: [держать / докупать / наблюдать — твоё решение]",
                  "", "Это мой личный разбор, не инвестиционная рекомендация."]
        return Draft("paper", monday.isoformat(), "\n".join(parts))

    def glossary(self, today: date) -> Draft:
        monday, _ = current_week(today)
        idx = (today.isocalendar().year * 53 + today.isocalendar().week - 1) % len(TERMS)
        title, body = TERMS[idx]
        return Draft("glossary", monday.isoformat(), f"📚 Ликбез за 2 минуты\n\nСегодня разбираем: {title}\n\n{body}\n\nА какой термин разобрать следующим? 👇")

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
