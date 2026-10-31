"""Manual creator content planning, kept separate from investment and news drafts."""
from __future__ import annotations
import sqlite3
from datetime import date, timedelta
from pathlib import Path

PROJECTS = {
    'nova': ('NOVA', 'AI-ассистент', 'Логика, голос, интерфейс, эксперименты'),
    'helmet': ('Шлем Железного человека', 'Механика и электроника', '3D-печать, маска, сервоприводы, тесты'),
    'spider': ('Робо-паук', 'Робототехника', 'Проектирование, механизмы, движение, управление'),
}
SLOTS = {0: ('project_log', 'Видео · процесс'), 2: ('carousel', 'Карусель · объяснение'), 4: ('maker_vibe', 'Видео · атмосфера')}
STATUSES = ('idea', 'script', 'shooting', 'editing', 'ready', 'published')
SEEDS = [
    ('helmet', 0, 'Что сейчас происходит со шлемом', 'Снять детали, сборку и один заметный результат'),
    ('helmet', 2, 'Из чего состоит механизм шлема', '6 слайдов: корпус, петли, приводы, питание, код, следующий шаг'),
    ('helmet', 4, 'Смотрю сериал и собираю шлем', 'Атмосферный ролик: сериал на фоне, работа руками, без обязательного объяснения'),
    ('nova', 0, 'NOVA: что уже умеет ассистент', 'Демонстрация команды, ответа и текущего интерфейса'),
    ('nova', 2, 'Как устроен мой AI-ассистент', 'Схема: голос, обработка, логика, действия, интерфейс, планы'),
    ('nova', 4, 'Вечер программирования NOVA', 'Клавиатура, интерфейс, свет монитора и музыка'),
    ('spider', 0, 'Начинаю делать робо-паука', 'Первые эскизы, выбор конструкции, прототипирование'),
    ('spider', 2, 'Что нужно, чтобы робот-паук ходил', 'Ноги, степени свободы, сервоприводы, питание, управление'),
    ('spider', 4, 'Пятничная мастерская: детали робо-паука', 'Подбор деталей и работа над прототипом под фильм или музыку'),
    ('helmet', 0, 'Тест механизма маски', 'Показать испытания и доработки, даже если пока не идеально'),
    ('helmet', 2, 'Ошибки при 3D-печати подвижных деталей', 'Допуски, прочность, ориентация, крепления, повторная печать'),
    ('helmet', 4, 'Неспешный вечер в мастерской', 'Нарезка: шлем, инструменты, кино, детали'),
]

class CreatorStore:
    def __init__(self, path: Path):
        self.path = str(path)
        with self._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS creator_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                format TEXT NOT NULL,
                publish_date TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'idea',
                revision INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )''')
            db.execute('CREATE INDEX IF NOT EXISTS idx_creator_posts_date ON creator_posts(publish_date)')

    def _connect(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        return db

    def list(self, start: date, days: int = 42):
        end=start+timedelta(days=days)
        with self._connect() as db:
            return [dict(x) for x in db.execute('SELECT * FROM creator_posts WHERE publish_date>=? AND publish_date<? ORDER BY publish_date', (start.isoformat(),end.isoformat()))]

    def seed(self, start: date):
        monday=start-timedelta(days=start.weekday())
        inserted=0
        with self._connect() as db:
            for week in range(4):
                for weekday in (0,2,4):
                    key=week*3+(0 if weekday==0 else 1 if weekday==2 else 2)
                    project,_,title,notes = None,None,None,None
                    project,_,title,notes = (SEEDS[key][0],weekday,SEEDS[key][2],SEEDS[key][3])
                    when=(monday+timedelta(days=week*7+weekday)).isoformat()
                    cur=db.execute('INSERT OR IGNORE INTO creator_posts(project,format,publish_date,title,notes) VALUES(?,?,?,?,?)',(project,SLOTS[weekday][0],when,title,notes))
                    inserted+=cur.rowcount
        return inserted

    def update(self, post_id: int, data: dict):
        if type(data.get('revision')) is not int: raise ValueError('Требуется revision')
        fields={k:data[k] for k in ('title','notes','status','project') if k in data}
        if 'status' in fields and fields['status'] not in STATUSES: raise ValueError('Недопустимый статус')
        if 'project' in fields and fields['project'] not in PROJECTS: raise ValueError('Неизвестный проект')
        if 'title' in fields and (not isinstance(fields['title'],str) or not fields['title'].strip() or len(fields['title'])>160): raise ValueError('Заголовок 1–160 символов')
        if 'notes' in fields and (not isinstance(fields['notes'],str) or len(fields['notes'])>6000): raise ValueError('Заметки слишком длинные')
        if not fields: raise ValueError('Нет изменений')
        assignments=', '.join(f'{k}=?' for k in fields)
        with self._connect() as db:
            cur=db.execute(f'UPDATE creator_posts SET {assignments}, revision=revision+1 WHERE id=? AND revision=?',(*fields.values(),post_id,data['revision']))
            if cur.rowcount!=1: return None
            return dict(db.execute('SELECT * FROM creator_posts WHERE id=?',(post_id,)).fetchone())
