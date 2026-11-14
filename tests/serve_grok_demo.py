"""Explicit standalone UI fixture. Never run against a real database or real broker."""
from pathlib import Path
import os
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


if __name__ == '__main__':
    import uvicorn
    for key in ('TINVEST_TOKEN', 'XAI_API_KEY', 'TRADING_AI_API_KEY', 'TELEGRAM_BOT_TOKEN'):
        os.environ[key] = ''
    os.environ['TRADING_AI_PROVIDER'] = 'none'
    os.environ['GROK_CONNECTOR_TOKEN'] = 'demo-not-a-real-secret-'*3
    os.environ['TECH_TG_AUTO_PUBLISH'] = 'false'
    from invest_blog.dashboard import create_app
    from invest_blog.editorial_workflow import EditorialWorkflow
    from invest_blog.grok_mcp import create_connector
    import invest_blog.trading_routes as routes
    EditorialWorkflow._find_online_titles = lambda self: ('', '')
    class Market:
        def universe(self, clock):
            return {'uids': ['demo'], 'eligible_contracts': 1, 'selection': 'Демонстрационная выборка, вымышленные котировки.'}
        def snapshot(self, uid, clock=None):
            clock = clock or datetime.now(timezone.utc)
            end = clock.replace(minute=clock.minute//15*15, second=0, microsecond=0)
            contract = {'uid': uid, 'ticker': 'DEMO', 'name': 'Тестовый фьючерс', 'tick': '1', 'tick_value': '1',
                        'margin_long': '500', 'margin_short': '600', 'expires_at': (clock+timedelta(days=100)).isoformat()}
            return {'contract': contract, 'price': '135', 'quoted_at': clock.isoformat(), 'tradeable': True,
                    'candles': [{'time': (end-timedelta(minutes=(35-i)*15)).isoformat(),
                    'open': str(100+i), 'close': str(100+i), 'high': str(100+i+.1), 'low': str(100+i-.1)} for i in range(35)]}
    market = Market()
    routes.market = lambda: market
    with TemporaryDirectory(prefix='content-hub-grok-demo-') as folder:
        path = Path(folder)/'demo.db'
        mcp_server = uvicorn.Server(uvicorn.Config(create_connector(path, lambda: market), host='127.0.0.1', port=8770, log_level='warning'))
        thread = Thread(target=mcp_server.run, daemon=True)
        thread.start()
        try:
            uvicorn.run(create_app(path), host='127.0.0.1', port=8768, log_level='warning')
        finally:
            mcp_server.should_exit = True
            thread.join(timeout=10)
