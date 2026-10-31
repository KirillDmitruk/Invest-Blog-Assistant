# Content Hub: VK новости, 4 поста в день

## Переводчик без LLM API

```bash
uv add argostranslate
uv run python - <<'PY'
import argostranslate.package
packages = argostranslate.package.get_available_packages()
en_ru = next((p for p in packages if p.from_code == 'en' and p.to_code == 'ru'), None)
if en_ru is None:
    raise SystemExit('Argos en-ru не найден')
argostranslate.package.install_from_path(en_ru.download())
print('Переводчик готов')
PY
```

Установка модели требует интернета один раз. Перевод выполняется локально. Используются только краткие RSS-описания, не полные статьи; качество русского может требовать правки.

## Проверка без публикации

```bash
uv run python -m invest_blog.cli tech-news
uv run python -m invest_blog.cli vk-news --limit 4
```

Оцени качество текстов до включения автопубликации.

## Включение

В `.env`:

```dotenv
VK_AUTO_PUBLISH=true
VK_NEWS_TIMES=10:00,13:00,16:00,19:00
```

ID и токен VK должны быть уже заполнены. Часовой пояс определяется `TIMEZONE` из существующих настроек.

Сегодня, после проверки предварительных текстов, отправь до четырёх постов явно:

```bash
uv run python -m invest_blog.cli vk-news --limit 4 --publish --confirm PUBLISH
```

Это **реальные публичные** посты. Чтобы завтра работало автоматически, запусти `make bot` и не запускай второй экземпляр бота. Публикации происходят в указанные четыре временных слота, пропущенные слоты не восстанавливаются.

Лимит на день — 4, включая публикации, для которых VK не подтвердил результат. В таблице `vk_news_publications` сохраняются статусы, чтобы при сбоях не дублировать записи. Публикации без изображений. Для остановки поставь `VK_AUTO_PUBLISH=false` и перезапусти бота.
