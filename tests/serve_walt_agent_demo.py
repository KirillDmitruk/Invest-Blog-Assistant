"""Explicit standalone local analyst UI fixture, no real wallet access."""
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

if __name__=='__main__':
    import uvicorn
    os.environ['TECH_TG_AUTO_PUBLISH']='false';os.environ['VK_AUTO_PUBLISH']='false'
    from invest_blog.dashboard import create_app
    from invest_blog.editorial_workflow import EditorialWorkflow
    EditorialWorkflow._find_online_titles=lambda self:('','')
    with TemporaryDirectory(prefix='walt-agent-demo-') as folder:
        uvicorn.run(create_app(Path(folder)/'demo.db'),host='127.0.0.1',port=8766,log_level='warning')
