"""Readiness metadata for a PAPER worker; no broker or model requests."""
import json
import sys
from datetime import datetime, timedelta, timezone

from invest_blog.config import Settings
from invest_blog.trading_lab import TradingLab


if __name__ == '__main__':
    settings = Settings.load()
    TradingLab(settings.db_path)
    print(json.dumps({'python': sys.executable, 'channel': 'Trading Lab · PAPER only',
                      'next_at': (datetime.now(timezone.utc)+timedelta(minutes=5)).astimezone(settings.timezone).isoformat()}))
