"""Isolated stateless Streamable HTTP MCP endpoint; never mount on the full dashboard."""
import json
import os
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from .trading_connector import AnalysisDesk, ConnectorProposal


VERSIONS = ('2025-11-25', '2025-06-18', '2025-03-26')


def create_connector(path, market_factory, token=None):
    secret = token if token is not None else os.getenv('GROK_CONNECTOR_TOKEN', '')
    if len(secret) < 32:
        raise ValueError('Нужен отдельный GROK_CONNECTOR_TOKEN длиной не менее 32 символов. Команда: .\\hub grok-key')
    desk = AnalysisDesk(path)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def protect(request, call_next):
        # URL/query keys are deliberately unsupported: they leak into logs and browser history.
        auth = request.headers.get('authorization', '')
        if not secrets.compare_digest(auth.encode(), ('Bearer '+secret).encode()):
            return JSONResponse({'error': 'unauthorized'}, status_code=401,
                                headers={'WWW-Authenticate': 'Bearer'})
        origin = request.headers.get('origin')
        public = os.getenv('GROK_CONNECTOR_PUBLIC_URL', '').rstrip('/')
        allowed = {public, 'https://grok.com'}
        if origin and origin not in allowed:
            return JSONResponse({'error': 'origin denied'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/mcp')
    async def no_sse():
        return Response(status_code=405, headers={'Allow': 'POST'})

    @app.post('/mcp')
    async def endpoint(request: Request):
        if request.headers.get('mcp-protocol-version', '2025-03-26') not in VERSIONS:
            return JSONResponse({'error': 'unsupported protocol version'}, status_code=400)
        if 'application/json' not in request.headers.get('content-type', ''):
            return Response(status_code=415)
        size, chunks = 0, []
        async for chunk in request.stream():
            size += len(chunk)
            if size > 32768:
                return Response(status_code=413)
            chunks.append(chunk)
        try:
            body = json.loads(b''.join(chunks))
        except (ValueError, UnicodeError):
            return JSONResponse({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}})
        ident = body.get('id') if isinstance(body, dict) else None
        def error(code, message):
            return JSONResponse({'jsonrpc': '2.0', 'id': ident, 'error': {'code': code, 'message': message}})
        if not isinstance(body, dict) or body.get('jsonrpc') != '2.0' or not isinstance(body.get('method'), str):
            return error(-32600, 'Invalid request')
        if 'id' not in body:
            return Response(status_code=202)  # Initialized/cancelled notifications: no side effects.
        method, params = body['method'], body.get('params', {})
        if not isinstance(params, dict):
            return error(-32602, 'Invalid params')
        if method == 'initialize':
            offered = params.get('protocolVersion')
            result = {'protocolVersion': offered if offered in VERSIONS else VERSIONS[0],
                      'capabilities': {'tools': {'listChanged': False}},
                      'serverInfo': {'name': 'content-hub-paper', 'version': '1.0'},
                      'instructions': 'PAPER only. Read the user-prepared market packet, compare candidates, '
                          'then submit exactly one decision. No broker orders, secrets, real accounts or withdrawals.'}
        elif method == 'ping':
            result = {}
        elif method == 'tools/list':
            result = {'tools': [
                {'name': 'get_prepared_market_analysis', 'description': 'Read the latest user-prepared, expiring futures market sample and PAPER account. Does not fetch additional instruments.',
                 'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
                 'annotations': {'readOnlyHint': True, 'openWorldHint': False}},
                {'name': 'submit_paper_decision', 'description': 'Return one decision for a prepared analysis. May execute a VIRTUAL entry/close only if local risk controls permit. Use hold when no justified trade. No sizing, real orders or risk changes.',
                 'inputSchema': ConnectorProposal.model_json_schema(),
                 'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}}]}
        elif method == 'tools/call':
            name, arguments = params.get('name'), params.get('arguments', {})
            if not isinstance(arguments, dict):
                return error(-32602, 'Arguments must be an object')
            try:
                if name == 'get_prepared_market_analysis':
                    if arguments:
                        return error(-32602, 'This tool takes no arguments')
                    data = await run_in_threadpool(desk.read_packet)
                elif name == 'submit_paper_decision':
                    proposal = ConnectorProposal.model_validate(arguments)
                    data = await run_in_threadpool(desk.submit, proposal, market_factory())
                else:
                    return error(-32602, 'Unknown tool')
                result = {'content': [{'type': 'text', 'text': json.dumps(data, ensure_ascii=False)}], 'isError': False}
            except ValidationError:
                result = {'content': [{'type': 'text', 'text': 'Invalid proposal; use only the declared schema.'}], 'isError': True}
            except ValueError as exc:
                result = {'content': [{'type': 'text', 'text': str(exc)}], 'isError': True}
            except Exception:
                result = {'content': [{'type': 'text', 'text': 'Local check failed. No new decision accepted; inspect the hub and retry.'}], 'isError': True}
        else:
            return error(-32601, 'Method not found')
        return JSONResponse({'jsonrpc': '2.0', 'id': ident, 'result': result})
    return app


def serve(path, port=8769):
    import uvicorn
    from .trading_routes import market
    uvicorn.run(create_connector(path, market), host='127.0.0.1', port=port, access_log=False)
