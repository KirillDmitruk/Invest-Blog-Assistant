"""Read-only readiness check for the Windows worker launcher."""
import json
import sys

from invest_blog.config import Settings
from invest_blog.tech_direct import DirectSchedule, direct_mode
from invest_blog.tech_telegram import TechTelegramPublisher, _enabled
from invest_blog.vk_news import LocalTranslator
import os


def main():
    settings = Settings.load()
    if not direct_mode() or not _enabled('TECH_TG_AUTO_PUBLISH'):
        raise SystemExit('Включи TECH_TG_MODE=direct и TECH_TG_AUTO_PUBLISH=true в .env')
    try:
        schedule = DirectSchedule.from_env()
        info = TechTelegramPublisher(os.getenv('TELEGRAM_BOT_TOKEN', ''),
                                     os.getenv('TECH_TELEGRAM_CHANNEL_ID', '')).check_channel()
        LocalTranslator()
    except Exception:
        raise SystemExit('Не готово: проверь токен, права канала и модель EN→RU (.\\hub translation-model).') from None
    from datetime import datetime
    print(json.dumps({'python': sys.executable, 'channel': info['title'],
                      'next_at': schedule.next_at(datetime.now(settings.timezone)).isoformat()}, ensure_ascii=True))


if __name__ == '__main__':
    main()
