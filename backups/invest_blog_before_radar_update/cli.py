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
    parser.add_argument("action", choices=("bot", "accounts", "preview", "diagnose", "sync", "web", "audit", "bonds", "backfill", "risk", "performance", "placements", "tech-news"))
    parser.add_argument("kind", nargs="?", choices=("weekly", "monthly", "plan", "paper", "glossary", "purchase"))
    parser.add_argument("--date", dest="date_string", help="Локальная дата YYYY-MM-DD для preview (брокерские данные — актуальные)")
    parser.add_argument("--start", help="Начало периода YYYY-MM-DD (для diagnose)")
    parser.add_argument("--end", help="Конец периода YYYY-MM-DD (для diagnose)")
    parser.add_argument("--days", type=int, default=365, help="Для sync: глубина импорта исполненных операций")
    parser.add_argument("--port", type=int, default=8000, help="Для web: порт localhost")
    parser.add_argument("--partial", action="store_true", help="Для backfill: восстановить только подтверждённую часть истории")
    args = parser.parse_args()
    settings = Settings.load()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.action == "bot":
        from .bot import InvestBot
        InvestBot(settings).run()
        return
    if args.action == "web":
        import uvicorn
        from .dashboard import create_app
        print(f"Открой http://127.0.0.1:{args.port} (только локальный доступ)")
        uvicorn.run(create_app(settings.db_path, settings.bond_target,
                               settings.max_position_weight, settings.timezone, settings.times), host="127.0.0.1", port=args.port)
        return
    if args.action == "tech-news":
        import json
        from .tech_news import TechNewsStore, TechNewsCollector
        store=TechNewsStore(settings.db_path)
        report=TechNewsCollector(store).scan()
        print(json.dumps({'scan':report,'stats':store.stats()},ensure_ascii=False,indent=2))
        return
    if args.action == "placements":
        import json
        from .placements import PlacementMonitor, PlacementStore
        settings.validate_api()
        store = PlacementStore(settings.db_path)
        report = PlacementMonitor(store, TInvest(settings.invest_token, settings.timezone),
                                  feed_url=settings.placements_feed_url, finam_enabled=settings.placements_finam_enabled).scan()
        print(json.dumps({'scan': report, 'sources': store.status(),
                          'upcoming': store.upcoming(datetime.now(settings.timezone).date())},
                         ensure_ascii=False, indent=2))
        return
    if args.action == "performance":
        from .portfolio_store import PortfolioStore
        from .performance import summarize
        import json
        report = summarize(PortfolioStore(settings.db_path),
                           start=date.fromisoformat(args.start) if args.start else None,
                           end=date.fromisoformat(args.end) if args.end else None)
        print(json.dumps({'period': report.get('period'), 'comparisons': report.get('comparisons'),
                          'notes': report.get('notes')}, ensure_ascii=False, indent=2))
        return
    if args.action == "risk":
        from .portfolio_store import PortfolioStore
        from .risk import analyze_portfolio
        import json
        result = analyze_portfolio(PortfolioStore(settings.db_path),
                                   bond_target=settings.bond_target,
                                   max_position=settings.max_position_weight,
                                   today=datetime.now(settings.timezone).date())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.action == "bonds":
        from .portfolio_store import PortfolioStore
        from .bond_analytics import get_bond_analytics
        import json
        report = get_bond_analytics(PortfolioStore(settings.db_path), datetime.now(settings.timezone).date())
        if not report.get('has_data'):
            print(report['message'])
        else:
            cal = report['calendar']
            print(f"Облигаций: {report['bond_count']}; период: {report['calendar_from']} — {report['calendar_to_exclusive']} (правая граница не включена)")
            print(f"Календари: {cal['covered_bonds']}/{report['bond_count']}; пропущены: {len(cal['missing_bonds'])}; устарели: {len(cal['stale_bonds'])}")
            print('Ожидаемые известные купоны по валютам:',json.dumps(cal['known_total'],ensure_ascii=False))
            for e in cal['events'][:30]:
                print(f"{e['date']} {e['name']}: {e['amount'] if e['amount'] is not None else 'сумма неизвестна'} {e['currency'] or ''}")
        return
    if args.action == "audit":
        from .integrity import check_integrity
        from .portfolio_store import PortfolioStore
        import json
        result = check_integrity(PortfolioStore(settings.db_path))
        print(json.dumps(result, ensure_ascii=False, indent=2))
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
    if args.action == "backfill":
        from .portfolio_store import PortfolioStore
        from .history import HistoricalBackfill
        result=HistoricalBackfill(api,settings.account_ids,PortfolioStore(settings.db_path)).run(days=args.days, partial=args.partial)
        print(f"Восстановление {result['period']}: точек {result['points']}, пропусков {result['days_missing']}")
        for warning in result['warnings']:
            print('⚠️',warning)
        return
    if args.action == "sync":
        from .portfolio_store import PortfolioStore
        from .sync import PortfolioSync
        report = PortfolioSync(api, settings.account_ids, PortfolioStore(settings.db_path)).run(
            datetime.now(settings.timezone).date(), days=args.days)
        print(f"Снимок {report['day']}: счетов {report['accounts']}, позиций {report['positions']}, операций прочитано {report['operations']}")
        print(f"Карточек облигаций обновлено: {report['bond_profiles_updated']}, календарей купонов: {report['coupon_calendars_updated']}")
        for note in report['warnings']:
            print(f"⚠️ {note}")
        return
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
