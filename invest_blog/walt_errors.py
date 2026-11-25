"""Bounded, secret-free diagnostics for MCP errors. Never retain server prose."""
import re

CODES=frozenset(('confirmation_required','confirmation_expired','confirmation_mismatch','action_in_progress',
    'risk_validation_failed','agent.position.open_already_exists','agent.open_already_pending',
    'agent.trading_disabled','agent.margin.invalid_amount','agent.incorrect.order_params',
    'agent.account.creation_failed','agent.account.not_found','agent.account.not_ready',
    'mcp.forbidden_scope','mcp.invalid_argument','OPEN_POSITION_CREATE_ORDER_FAILED','INTERNAL_ERROR'))


def error_code(value):
    # Only public documented codes may leave the transport; arbitrary messages,
    # request payloads, OAuth tokens and confirmation tokens must never be logged.
    raw=str(value)
    for code in sorted(CODES,key=len,reverse=True):
        if re.search(r'(?<![A-Za-z0-9_.])'+re.escape(code)+r'(?![A-Za-z0-9_.])',raw):return code
    if isinstance(value,dict) and isinstance(value.get('code'),int):return str(value['code'])
    return None


class WaltRPCError(ValueError):
    def __init__(self,kind,code=None,http_status=None):
        self.diagnostic={'kind':kind,'code':code,'http_status':http_status}
        label={'timeout':'Walt не ответил вовремя','connection':'Соединение с Walt прервалось',
            'http':'Ошибка HTTP от Walt','rpc':'Ошибка протокола MCP от Walt',
            'tool':'Walt вернул ошибку операции','format':'Не удалось разобрать ответ Walt'}.get(kind,'Сбой обработки Walt')
        super().__init__(label+(f' · {code}' if code else '')+(f' · HTTP {http_status}' if http_status else '')+'. Автоматического повтора нет.')
