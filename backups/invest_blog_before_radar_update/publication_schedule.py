"""Single source of truth for recurring draft-generation schedule.

These jobs create draft posts for review, not automatic channel publications.
"""
from __future__ import annotations

LABELS = {
    'weekly': 'Еженедельный отчёт',
    'monthly': 'Ежемесячный отчёт',
    'plan': 'План на неделю',
    'paper': 'Бумага недели',
    'glossary': 'Ликбез за 2 минуты',
    'purchase': 'Пост о покупке',
}

# kind, Python weekday (Monday=0), APScheduler day-of-week
WEEKLY_RULES = (
    ('plan', 0, 'mon'),
    ('paper', 2, 'wed'),
    ('glossary', 4, 'fri'),
    ('weekly', 5, 'sat'),
)
WEEKDAY_NAMES = (
    'Понедельник', 'Вторник', 'Среда', 'Четверг',
    'Пятница', 'Суббота', 'Воскресенье',
)
DEFAULT_TIMES = {
    'plan': (10, 0),
    'paper': (10, 0),
    'glossary': (10, 0),
    'weekly': (10, 0),
    'monthly': (11, 0),
}


def recurring_schedule(times: dict[str, tuple[int, int]] | None = None,
                       timezone: str = 'Europe/Moscow') -> dict:
    """Public view; contains no tokens, account IDs or channel information."""
    merged = {**DEFAULT_TIMES, **(times or {})}

    def display_time(kind: str) -> str:
        hour, minute = merged[kind]
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError(f'Некорректное время для {kind}')
        return f'{hour:02d}:{minute:02d}'

    week = [{'weekday': i, 'name': day, 'items': []}
            for i, day in enumerate(WEEKDAY_NAMES)]
    for kind, weekday, _ in WEEKLY_RULES:
        week[weekday]['items'].append({
            'kind': kind, 'label': LABELS[kind], 'time': display_time(kind),
        })
    return {
        'timezone': timezone,
        'weekdays': week,
        'monthly': {'day': 1, 'kind': 'monthly',
                    'label': LABELS['monthly'], 'time': display_time('monthly')},
        'note': 'Это время автоматического создания черновиков ботом, не публикации в канал.',
    }
