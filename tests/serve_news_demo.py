"""Standalone investment newsroom demo on a temporary DB; no real network or sends."""
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

if __name__=='__main__':
    import uvicorn
    os.environ.update(TECH_TG_MODE='direct',TECH_TG_AUTO_PUBLISH='true',TECH_TELEGRAM_CHANNEL_ID='@kirintechnews',
        TECH_TG_DAILY_LIMIT='28',TECH_TG_START_HOUR='9',TECH_TG_END_HOUR='23',VK_AUTO_PUBLISH='false')
    from invest_blog.dashboard import create_app
    from invest_blog.tech_news import TechNewsStore,TechNewsCollector
    from invest_blog.editorial_workflow import EditorialWorkflow
    EditorialWorkflow._find_online_titles=lambda self:('','')
    TechNewsCollector.scan=lambda self:[{'source':'Демо','status':'ok','found':0,'added':0}]
    with TemporaryDirectory(prefix='investment-news-demo-') as folder:
        path=Path(folder)/'demo.db'
        TechNewsStore(path).save([{'source':'Демонстрация','title':'Компания представила финансовые результаты за отчётный квартал',
            'summary':'В тестовом примере компания сообщила об изменении выручки и прибыли. Это вымышленные данные для проверки интерфейса, а не реальная новость.',
            'url':'https://example.org/demo','category':'russia','published_at':datetime.now(timezone.utc).isoformat()}])
        uvicorn.run(create_app(path),host='127.0.0.1',port=8767,log_level='warning')
