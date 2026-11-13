from __future__ import annotations

from dataclasses import dataclass
from os import getenv
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .publication_schedule import DEFAULT_TIMES


def _time(name: str, fallback: str) -> tuple[int, int]:
    raw = getenv(name, fallback)
    try:
        hour_s, minute_s = raw.split(":")
        hour, minute = int(hour_s), int(minute_s)
        if not 0 <= hour < 24 or not 0 <= minute < 60:
            raise ValueError()
        return hour, minute
    except ValueError as exc:
        raise ValueError(f"{name} должен иметь формат HH:MM, получено {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    invest_token: str
    account_ids: tuple[str, ...]
    telegram_token: str
    telegram_chat_id: int | None
    timezone: ZoneInfo
    db_path: Path
    cbr_path: Path
    startup_catchup: bool
    times: dict[str, tuple[int, int]]
    telegram_channel_id: str = ""
    auto_snapshot: bool = False
    bond_target: float = .60
    max_position_weight: float = .15
    placements_feed_url: str = ""
    placements_finam_enabled: bool = True
    iis_review_enabled: bool = True
    iis_review_interval_days: int = 3
    iis_review_account_id: str = ""

    @classmethod
    def load(cls) -> Settings:
        load_dotenv()
        chat = getenv("TELEGRAM_CHAT_ID", "").strip()
        review_days = int(getenv('IIS_REVIEW_INTERVAL_DAYS', '3'))
        if not 1 <= review_days <= 30:
            raise ValueError('IIS_REVIEW_INTERVAL_DAYS должен быть от 1 до 30')
        return cls(
            invest_token=getenv("TINVEST_TOKEN", "").strip(),
            account_ids=tuple(x.strip() for x in getenv("TINVEST_ACCOUNT_IDS", "").split(",") if x.strip()),
            telegram_token=getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_id=int(chat) if chat else None,
            timezone=ZoneInfo(getenv("TIMEZONE", "Europe/Moscow")),
            db_path=Path(getenv("DB_PATH", "./data/drafts.sqlite3")),
            cbr_path=Path(getenv("CBR_EVENTS_PATH", "./data/cbr_events.json")),
            startup_catchup=getenv("STARTUP_CATCHUP", "true").lower() in ("1", "true", "yes"),
            telegram_channel_id=getenv("TELEGRAM_CHANNEL_ID", "").strip(),
            auto_snapshot=getenv("AUTO_SNAPSHOT", "false").lower() in ("1", "true", "yes"),
            bond_target=float(getenv("BOND_TARGET", ".60")),
            max_position_weight=float(getenv("MAX_POSITION_WEIGHT", ".15")),
            placements_feed_url=getenv("PLACEMENTS_FEED_URL", "").strip(),
            placements_finam_enabled=getenv("PLACEMENTS_FINAM_ENABLED", "true").lower() in ("1", "true", "yes"),
            iis_review_enabled=getenv('IIS_REVIEW_ENABLED', 'true').lower() in ('1', 'true', 'yes'),
            iis_review_interval_days=review_days,
            iis_review_account_id=getenv('IIS_REVIEW_ACCOUNT_ID', '').strip(),
            times={kind: _time(f'{kind.upper()}_TIME', f'{h:02d}:{m:02d}')
                   for kind, (h, m) in DEFAULT_TIMES.items()},
        )

    def validate_api(self) -> None:
        if not self.invest_token:
            raise ValueError("Укажи TINVEST_TOKEN в .env")

    def validate_bot(self) -> None:
        self.validate_api()
        if not self.account_ids:
            raise ValueError("Укажи TINVEST_ACCOUNT_IDS в .env. Узнать ID: python -m invest_blog.cli accounts")
        if not self.telegram_token:
            raise ValueError("Укажи TELEGRAM_BOT_TOKEN в .env")
        if self.telegram_chat_id is None:
            raise ValueError("Укажи TELEGRAM_CHAT_ID. Напиши /whoami своему боту")

