"""Shared, local writing tools. Translation never changes or publishes a draft."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field


class TranslationInput(BaseModel):
    text: str = Field(min_length=1, max_length=10000)


def register_editor_tools(app, require_editor):
    router = APIRouter(prefix='/api/editor/tools')

    @router.post('/translate')
    def translate(payload: TranslationInput, request: Request):
        require_editor(request)
        try:
            from .vk_news import LocalTranslator
            result = LocalTranslator()(payload.text)
        except RuntimeError as exc:
            raise HTTPException(503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(503, detail='Локальный перевод недоступен') from exc
        return {'text': result, 'source': 'en', 'target': 'ru'}

    app.include_router(router)
