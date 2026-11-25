"""Local UI routes for Walt + local Ollama; OAuth scope and execution are read-only."""
import logging
from typing import Annotated
from fastapi import APIRouter,HTTPException,Request,Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel,ConfigDict,Field
from starlette.concurrency import run_in_threadpool
from .walt_agent import WaltAgent


class CallbackLogFilter(logging.Filter):
    def filter(self,record):
        if isinstance(record.args,tuple) and len(record.args)>=3 and isinstance(record.args[2],str) and record.args[2].startswith('/api/walt-agent/callback'):
            args=list(record.args);args[2]='/api/walt-agent/callback';record.args=tuple(args)
        return True


class AgentSettings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int=Field(ge=1,strict=True)
    model:str=Field(min_length=3,max_length=100)
    allowed_tools:list[str]=Field(max_length=30)


class Analysis(BaseModel):
    model_config=ConfigDict(extra='forbid')
    prompt:str=Field(min_length=10,max_length=3000)


def register_walt_agent_routes(app,path,require_editor):
    agent=WaltAgent(path)
    router=APIRouter(prefix='/api/walt-agent')
    logger=logging.getLogger('uvicorn.access')
    if not any(isinstance(f,CallbackLogFilter) for f in logger.filters):logger.addFilter(CallbackLogFilter())

    @router.get('/overview')
    def overview():return agent.overview()

    @router.post('/model-check')
    def model_check(request:Request):
        require_editor(request);return agent.model_status()

    @router.post('/auth/start')
    def auth_start(request:Request):
        require_editor(request)
        try:return agent.start_auth(str(request.base_url))
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc

    @router.get('/callback',response_class=HTMLResponse)
    def callback(code:Annotated[str,Query(max_length=2048)]='',state:Annotated[str,Query(max_length=200)]='',error:Annotated[str,Query(max_length=200)]=''):
        message='Авторизация не завершена. Вернись в хаб и начни подключение заново.';status=400
        if code and state and not error:
            try:
                agent.finish_auth(code,state);message='Walt подключён для чтения. Вернись в хаб, открой «Walt · ИИ-агент» и загрузи список инструментов.';status=200
            except ValueError:pass
        elif not code and not state and not error and agent.overview()['connected']:
            message='Авторизация Walt уже сохранена. Можно вернуться в хаб.';status=200
        return HTMLResponse('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="referrer" content="no-referrer"><title>Подключение Walt</title><body><h1>'+message+'</h1><a href="/#walt-agent">Вернуться к агенту Walt</a><script>history.replaceState(null,"","/api/walt-agent/callback")</script></body></html>',status_code=status,
                            headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})

    @router.post('/disconnect')
    def disconnect(request:Request):
        require_editor(request);agent.disconnect()
        return {'note':'Локальная авторизация удалена. Это не закрывает позиции и не останавливает агентский счёт в Walt. Для полного отзыва проверь подключение в Walt.'}

    @router.post('/tools')
    def tools(request:Request):
        require_editor(request)
        try:return {'tools':agent.discover()}
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc

    @router.put('/settings')
    def settings(payload:AgentSettings,request:Request):
        require_editor(request)
        try:agent.configure(payload.revision,payload.model,payload.allowed_tools);return {'saved':True}
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc

    @router.post('/analyze')
    async def analyze(payload:Analysis,request:Request):
        require_editor(request)
        try:return await run_in_threadpool(agent.analyze,payload.prompt.strip())
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc

    app.include_router(router)
