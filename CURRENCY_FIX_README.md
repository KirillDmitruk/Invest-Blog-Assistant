# Исправление backfill: позиция currency (v1.2)

## Причина

Метод T-Invest `GetPortfolio` может вернуть рублёвые средства как отдельную позицию `instrumentType=currency` (например, `RUB000UTSTOM`). При этом денежный остаток **уже включён** в `totalAmountCurrencies`, который наш проект сохраняет в `portfolio_snapshots.cash_rub`. Старая версия `history.py` принимала только `bond/share/etf` и завершалась с ошибкой `Неподдерживаемый тип позиции currency`.

## Что изменено

- `invest_blog/history.py`: распознаётся подтверждённая рублёвая денежная позиция (идентификатор/тикер RUB или RUB000UTSTOM, либо явное русское/английское название рубля; цена строго **1 RUB**). Она **не прибавляется** отдельно к стоимости портфеля, чтобы деньги не учитывались дважды.
- Валюты USD, CNY и иные неопознанные денежные позиции намеренно вызывают информативное исключение. Значение `position.currency=RUB` может означать только валюту котировки, а не сам денежный актив. Без FX-истории нельзя надёжно оценить их прошлую рублёвую стоимость.
- `tests/test_history.py`: тесты отсутствия двойного учёта рублей и защиты от валют без конвертации.

## Установка

Останови команду `bot` и веб-сервер (если запущены) и выполни:

```bash
cd ~/Projects/invest_blog_assistant
cp invest_blog/history.py invest_blog/history.py.bak
unzip -o ~/Downloads/invest_blog_currency_fix.zip
uv run python -m pytest -q
uv run python -m invest_blog.cli backfill --days 365
```

При успешном восстановлении снова запусти веб-сервер:

```bash
uv run python -m invest_blog.cli web --port 8000
```

Открой http://127.0.0.1:8000 и обнови страницу.

## Если получишь новое сообщение об FX-курсах

Это означает, что в портфеле кроме рублей есть иная денежная позиция (или она не была надёжно распознана как RUB). Чтобы безопасно посмотреть **только описание**, без ID счёта и секретов:

```bash
uv run python - <<'PY'
from invest_blog.config import Settings
from invest_blog.portfolio_store import PortfolioStore
settings = Settings.load()
with PortfolioStore(settings.db_path).connect() as db:
    rows = db.execute('''SELECT name,ticker,kind,price,currency
                         FROM position_snapshots WHERE lower(kind)='currency'
                         ORDER BY day DESC LIMIT 15''').fetchall()
for row in rows:
    print(dict(row))
PY
```

Не ставь `continue` для всех `currency`: это нарушит оценку прошлой стоимости при наличии USD/CNY.

## Примечания

Текущие снимки портфеля и все операции остаются неизменными. Backfill по-прежнему выдаёт **оценочную** историю, а не подтверждённую историческую стоимость брокера. Возможны следующие ограничения из прежней версии: неполные операции, пропущенные котировки, корпоративные действия, НКД, амортизация и валютные конвертации.
