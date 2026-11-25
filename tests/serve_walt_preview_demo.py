"""Actual native preview shape, mocked broker and no live credentials."""
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_walt_preview import native_service
from invest_blog.dashboard import create_app
from invest_blog.editorial_workflow import EditorialWorkflow
import invest_blog.walt_trade_routes as routes
import invest_blog.walt_agent_routes as agent_routes
import uvicorn

if __name__=='__main__':
    EditorialWorkflow._find_online_titles=lambda self:('','')
    with TemporaryDirectory(prefix='walt-native-preview-') as folder:
        service=native_service(Path(folder));service.signal()
        routes.WaltAgent=lambda _:service.agent
        routes.WaltTrading=lambda _:service
        agent_routes.WaltAgent=lambda _:service.agent
        uvicorn.run(create_app(service.agent.path),host='127.0.0.1',port=8767,log_level='warning')
