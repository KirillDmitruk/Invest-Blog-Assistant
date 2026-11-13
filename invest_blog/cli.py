"""CLI for Invest Blog Assistant."""

from __future__ import annotations
import sys

if sys.platform == "win32":
    import truststore

    truststore.inject_into_ssl()

import argparse
import logging
from collections import Counter, defaultdict
from decimal import Decimal
from datetime import date, datetime

from .config import Settings
from .posts import PostGenerator, operation_type, previous_month
from .tinvest import TInvest
from .model import money, fmt_money
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
    parser.add_argument("action", choices=("bot", "accounts", "preview", "diagnose", "sync", "web", "audit", "bonds", "backfill", "risk", "performance", "placements", "tech-news", "vk-test", "vk-news", "tg-news", "tg-test"))
    parser.add_argument("kind", nargs="?", choices=("weekly", "monthly", "plan", "paper", "glossary", "purchase"))
    parser.add_argument("--date", dest="date_string", help="Локальная дата YYYY-MM-DD для preview (брокерские данные — актуальные)")
    parser.add_argument("--start", help="Начало периода YYYY-MM-DD (для diagnose)")
    parser.add_argument("--end", help="Конец периода YYYY-MM-DD (для diagnose)")
    parser.add_argument("--days", type=int, default=365, help="Для sync: глубина импорта исполненных операций")
    parser.add_argument("--port", type=int, default=8000, help="Для web: порт localhost")
    parser.add_argument("--partial", action="store_true", help="Для backfill: восстановить только подтверждённую часть истории")
    parser.add_argument("--publish", action="store_true", help="Для vk-test: действительно отправить тестовый пост")
    parser.add_argument("--confirm", default="", help="Для vk-test: подтверждение словом PUBLISH")
    parser.add_argument("--limit", type=int, default=1, help="vk-news: от 1 до 4")
    args = parser.parse_args()
    settings = Settings.load()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.action == "tg-test":
        import os
        from .tech_telegram import TechTelegramPublisher
        token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
        channel = os.getenv('TECH_TELEGRAM_CHANNEL_ID', '').strip()
        if not token or not channel:
            parser.error('Укажи TELEGRAM_BOT_TOKEN и TECH_TELEGRAM_CHANNEL_ID в .env')
        if not args.publish:
            print(f'Telegram: проверка для {channel}. Отправка отключена.')
            print('Для реального теста: uv run python -m invest_blog.cli tg-test --publish --confirm PUBLISH')
            return
        if args.confirm != 'PUBLISH':
            parser.error('Для отправки требуется --confirm PUBLISH')
        print('Опубликовано:',TechTelegramPublisher(token, channel).send('Тестовая публикация Content Hub — Kir in Tech News.'))
        return
    if args.action == "tg-news":
        from .tech_telegram import sync
        import json
        if args.publish and args.confirm != "PUBLISH":
            parser.error("Нужно --confirm PUBLISH")
        print(json.dumps(sync(settings.db_path, publish=args.publish, limit=args.limit), ensure_ascii=False, indent=2))
        return
    if args.action == "vk-news":
        from .vk_news import run
        import json
        if args.publish and args.confirm != "PUBLISH":
            parser.error("Нужно --confirm PUBLISH")
        result = run(settings.db_path,settings.timezone,publish=args.publish,limit=args.limit)
        if args.publish:
            from .tech_telegram import sync
            if __import__('os').getenv('TECH_TG_AUTO_PUBLISH','false').lower() in ('true','1','yes'):
                result['telegram'] = sync(settings.db_path, publish=True, limit=args.limit)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return
    if args.action == "vk-test":
        from .vk_publisher import VKConfig, VKPublisher
        cfg = VKConfig.from_env()
        message = "Тестовая публикация Content Hub — проверка подключения Kir in Tech News."
        if not args.publish:
            print("VK: тестовый режим. Отправки нет.\n" + message)
            print("Для отправки: uv run python -m invest_blog.cli vk-test --publish --confirm PUBLISH")
            return
        if args.confirm != "PUBLISH":
            parser.error("Для тестовой отправки требуется --confirm PUBLISH")
        url = VKPublisher(cfg).publish(message, guid="content-hub-vk-connectivity-test-v1")
        print("Опубликовано:", url)
        return
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
