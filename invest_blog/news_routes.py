"""Simple investment newsroom controls; existing publishing ledger remains intact."""
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .tech_news import TechNewsStore, TechNewsCollector, TELEGRAM_SOURCES, canonical_url, clean_text


class Controls(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision: int=Field(ge=1,strict=True)
    enabled: bool=Field(strict=True)
    categories: list[Literal['russia','world','crypto']]=Field(min_length=1,max_length=3)


class ManualNews(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title: str=Field(min_length=12,max_length=250)
    summary: str=Field(min_length=60,max_length=350)
    url: str=Field(min_length=10,max_length=1000)
    category: Literal['russia','world','crypto']


def register_news_routes(app,path,require_editor):
    store=TechNewsStore(path)
    router=APIRouter(prefix='/api/news')

    @router.get('/controls')
    def controls():
        from .news_cloud_status import status
        return {**store.controls(), 'cloud':status(), 'telegram_sources':[{'name':name,'url':url,'mode':'manual'} for name,url in TELEGRAM_SOURCES]}

    @router.put('/controls')
    def update(payload:Controls,request:Request):
        require_editor(request)
        from .news_cloud_status import status
        if status()['enabled']:
            raise HTTPException(409,'Публикации перенесены в облако. Локальные переключатели расписанием не управляют; открой управление облаком.')
        try:
            return store.set_controls(payload.enabled,payload.categories,payload.revision)
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from exc

    @router.post('/refresh')
    async def refresh(request:Request):
        require_editor(request)
        report=await run_in_threadpool(TechNewsCollector(store).scan)
        return {'sources':report,'stats':store.stats()}

    @router.post('',status_code=201)
    def manual(payload:ManualNews,request:Request):
        require_editor(request)
        from .news_cloud_status import status
        if status()['enabled']:
            raise HTTPException(409,'Локальная очередь не отправляется в облако. Ручной импорт в облачную редакцию пока не подключён.')
        url=canonical_url(payload.url)
        if not url:
            raise HTTPException(422,'Нужна HTTPS-ссылка на оригинал, без логина и пароля.')
        parts=urlsplit(url)
        if parts.hostname in ('t.me','www.t.me'):
            segments=[p for p in parts.path.split('/') if p]
            if len(segments)!=2 or not segments[1].isdigit() or segments[0] in ('c','s','joinchat') or segments[0].startswith('+'):
                raise HTTPException(422,'Укажи ссылку на конкретный публичный пост: https://t.me/канал/123.')
        title,summary=clean_text(payload.title,250),clean_text(payload.summary,350)
        if len(title)<12 or len(summary)<60:
            raise HTTPException(422,'После очистки текста недостаточно для новости.')
        added=store.save([{'title':title,'summary':summary,'url':url,
            'source':('Telegram · @'+segments[0]) if parts.hostname in ('t.me','www.t.me') else parts.hostname,
            'category':payload.category,'published_at':None}])
        if not added:
            raise HTTPException(409,'Материал с такой ссылкой уже есть в очереди или истории.')
        return {'added':True,'note':'Добавлено в очередь. При включённой публикации материал может выйти в следующий слот.'}

    app.include_router(router)
