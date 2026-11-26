"""Minimal, opt-in VK community wall integration. No scheduler, no token logging."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests

API_VERSION = "5.199"
API_ROOT = "https://api.vk.com/method/"


class VKPublishError(RuntimeError):
    pass


@dataclass(frozen=True)
class VKConfig:
    group_id: int
    token: str
    auto_publish: bool = False

    @classmethod
    def from_env(cls) -> "VKConfig":
        raw_id = os.getenv("VK_GROUP_ID", "").strip()
        token = os.getenv("VK_ACCESS_TOKEN", "").strip()
        if not raw_id.isdigit() or int(raw_id) <= 0:
            raise ValueError("VK_GROUP_ID должен быть положительным числом без club и минуса")
        if not token:
            raise ValueError("VK_ACCESS_TOKEN не задан в .env")
        return cls(int(raw_id), token, os.getenv("VK_AUTO_PUBLISH", "false").lower() in ("1", "true", "yes"))


class VKPublisher:
    def __init__(self, config: VKConfig, session: Any = None):
        self.config = config
        self.session = session or requests.Session()

    def _call(self, method: str, **params: Any) -> dict:
        try:
            response = self.session.post(
                API_ROOT + method,
                data={**params, "access_token": self.config.token, "v": API_VERSION},
                timeout=20,
            )
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise VKPublishError("Ошибка соединения с VK API или некорректный ответ") from exc
        if not isinstance(result, dict):
            raise VKPublishError("Некорректный ответ VK API")
        if "error" in result:
            error = result["error"]
            code = error.get("error_code", "?") if isinstance(error, dict) else "?"
            # Never expose raw VK error_msg: it may include request context.
            raise VKPublishError(f"VK API отказал в запросе {method} (код {code}). Проверь права токена и ID группы.")
        data = result.get("response")
        if not isinstance(data, dict):
            raise VKPublishError("VK API не вернул ожидаемый объект response")
        return data

    def check(self) -> dict:
        items = self._call("groups.getById", group_ids=str(self.config.group_id))
        # groups.getById may return {groups: [...]} for newer API responses.
        return items

    def publish(self, text: str, *, guid: str, attachment_url: str | None = None) -> str:
        if not text.strip():
            raise ValueError("Нельзя опубликовать пустой текст")
        if not guid or len(guid) > 80:
            raise ValueError("Необходим стабильный идентификатор публикации")
        extra={}
        if attachment_url:
            from urllib.parse import urlsplit
            parts=urlsplit(attachment_url)
            if parts.scheme!='https' or not parts.hostname or parts.username or parts.password:raise ValueError('Invalid source link')
            extra['attachments']=attachment_url
        result = self._call(
            "wall.post",
            owner_id=-self.config.group_id,
            from_group=1,
            message=text,
            guid=guid,
            **extra,
        )
        post_id = result.get("post_id")
        if not isinstance(post_id, int) or post_id <= 0:
            raise VKPublishError("VK не вернул корректный post_id")
        return f"https://vk.com/wall-{self.config.group_id}_{post_id}"
