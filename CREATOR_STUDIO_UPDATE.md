# Kir in Tech · Creator Studio

Новый раздел Content Hub: три проекта (NOVA, шлем Железного человека, робо-паук), четыре недели по три публикации (понедельник — процесс, среда — карусель, пятница — атмосфера), редактор заголовков, заметок и статусов.

## Установка
1. Остановить веб-сервер.
2. Создать резервную копию исходников и SQLite.
3. `unzip -o ~/Downloads/content_hub_creator_studio_patch.zip`
4. `uv sync --extra dev`
5. `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run python -m pytest -q`
6. `make web`, далее Tech & Gadgets → Creator Studio → Подготовить 12 идей.

SQLite автоматически создаёт таблицу `creator_posts` в той же БД. Повторное создание плана не меняет существующие записи.

Это ручной планировщик — не загружает фото, не генерирует ролики, не публикует в Instagram/TikTok.
