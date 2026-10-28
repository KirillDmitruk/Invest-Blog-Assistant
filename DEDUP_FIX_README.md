# Invest Blog Assistant v1.2 — исправление дублей при повторном sync

Причина: T-Invest предупреждает, что `OperationItem.id` может меняться после повторной загрузки. Старый `ON CONFLICT` сохранял и старую, и новую копию сделки под разными хешами ID. Два одинаковых BUY в SQLite могли вызывать отрицательные остатки при восстановлении истории.

Изменено `PortfolioStore.save_batch`: при `sync` операции перезагружаемого счёта и интервала заменяются **в одной транзакции** с записью новых операций. Если API или запись завершились ошибкой, прежние записи остаются. Не трогаем операции других счетов и даты за пределами выбранного периода. Без `coverage_ranges` старое поведение сохраняется для тестов и локального импорта.

## Установка (Ubuntu, uv)

Остановить бот и веб-сервер. Из корня `~/Projects/invest_blog_assistant`:

```bash
mkdir -p backups
uv run python - <<'PY'
from pathlib import Path
from datetime import datetime
from sqlite3 import connect
from dotenv import dotenv_values
p=Path(dotenv_values('.env').get('DB_PATH') or 'data/drafts.sqlite3').expanduser()
if not p.exists(): raise SystemExit(f'Нет базы: {p}')
t=Path('backups')/f'pre_dedup_{datetime.now():%Y%m%d_%H%M%S}.sqlite3'
with connect(p) as source, connect(t) as dest: source.backup(dest)
print('Сохранено:',t)
PY
unzip -o ~/Downloads/invest_blog_dedup_fix.zip
uv run python -m pytest -q
```

### Проверка и очистка повторной синхронизацией

```bash
uv run python tools/inspect_trade_duplicates.py --suffix d803883c --day 2026-10-28
uv run python -m invest_blog.cli sync --days 365
uv run python tools/inspect_trade_duplicates.py --suffix d803883c --day 2026-10-28
uv run python -m invest_blog.cli backfill --days 365
```

**Важно:** `sync` целиком заменяет операции только того периода, который удалось полностью загрузить по текущим счетам. Не удаляйте записи SQL вручную. После синхронизации в БД должна остаться одна запись о покупке, *если брокер действительно вернул одну*. Если API по-прежнему возвращает две разные исполненные операции, проблема не подтверждается как дубль ID: сверяйте их trade numbers и брокерский отчёт, не удаляя сделки на основании одинакового времени. Нельзя удалять одну из двух реально исполненных сделок.

Полный график — расчётный, а не подтверждённая брокером историческая стоимость.
