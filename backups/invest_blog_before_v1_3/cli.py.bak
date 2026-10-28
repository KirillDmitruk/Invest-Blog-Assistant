from __future__ import annotations

import argparse
import logging
from collections import Counter, defaultdict
from decimal import Decimal
from datetime import date, datetime

from .config import Settings
from .posts import PostGenerator, operation_type, previous_month
from .tinvest import TInvest
from .model import money, fmt_money


def main() -> None:
    parser = argparse.ArgumentParser(description="Invest Blog Assistant")
    parser.add_argument("action", choices=("bot", "accounts", "preview", "diagnose"))
    parser.add_argument("kind", nargs="?", choices=("weekly", "monthly", "plan", "paper", "glossary", "purchase"))
    parser.add_argument("--date", dest="date_string", help="Локальная дата YYYY-MM-DD для preview (брокерские данные — актуальные)")
    parser.add_argument("--start", help="Начало периода YYYY-MM-DD (для diagnose)")
    parser.add_argument("--end", help="Конец периода YYYY-MM-DD (для diagnose)")
    args = parser.parse_args()
    settings = Settings.load()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.action == "bot":
        from .bot import InvestBot
        InvestBot(settings).run()
        return
    if args.action == "accounts":
        settings.validate_api()
        api = TInvest(settings.invest_token, settings.timezone)
        for account in api.accounts():
            print(f"{account.get('id')} | {account.get('name')} | {account.get('type')} | {account.get('status')}")
        return
    settings.validate_api()
    if not settings.account_ids:
        parser.error("Укажи TINVEST_ACCOUNT_IDS в .env")
    api = TInvest(settings.invest_token, settings.timezone)
    generator = PostGenerator(api, settings.account_ids, settings.cbr_path)
    day = date.fromisoformat(args.date_string) if args.date_string else datetime.now(settings.timezone).date()
    if args.action == "diagnose":
        start_default, end_default = previous_month(day)
        start = date.fromisoformat(args.start) if args.start else start_default
        end = date.fromisoformat(args.end) if args.end else end_default
        operations = generator.get_operations(start, end)
        counts = Counter(operation_type(op) for op in operations)
        totals = defaultdict(Decimal)
        for op in operations:
            amount, currency = money(op.get("payment"))
            totals[(operation_type(op), currency)] += amount
        print(f"Период: {start} — {end}; счетов: {len(settings.account_ids)}; операций: {len(operations)}")
        for typ, count in sorted(counts.items()):
            parts = [fmt_money(total, cur) for (key, cur), total in sorted(totals.items()) if key == typ]
            print(f"{typ}: {count} шт.; {', '.join(parts)}")
        if not operations:
            print("За период API не вернул операций. Проверь выбранный счёт и даты в Т-Инвестициях.")
        return
    if not args.kind:
        parser.error("Для preview укажи рубрику, например: preview weekly")
    print(generator.generate(args.kind, day).body)


if __name__ == "__main__":
    main()
