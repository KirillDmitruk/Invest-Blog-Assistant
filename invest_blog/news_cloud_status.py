"""Public cloud news heartbeat. No credentials or private state are read by the hub."""
import base64,json,os,time
import requests

CACHE={'until':0,'value':None}

def status():
    enabled=os.getenv('NEWS_CLOUD_ENABLED','false').lower()=='true'
    common={'enabled':enabled,'paused':os.getenv('NEWS_CLOUD_PAUSED','false').lower()=='true','channel':os.getenv('NEWS_TELEGRAM_CHANNEL','').strip(),'hours':'09:00–23:00 МСК','interval_minutes':30,
            'workflow_url':'https://github.com/KirillDmitruk/Invest-Blog-Assistant/actions/workflows/telegram-news.yml'}
    if not enabled:return common
    if CACHE['until']>time.monotonic():return {**common,**CACHE['value']}
    try:
        response=requests.get('https://api.github.com/repos/KirillDmitruk/Invest-Blog-Assistant/contents/news-status.json',
            params={'ref':'automation/telegram-news-state'},headers={'Accept':'application/vnd.github+json','Cache-Control':'no-cache'},timeout=(3,6))
        response.raise_for_status();body=response.json()
        if body.get('encoding')!='base64' or len(body.get('content',''))>100000:raise ValueError()
        value=json.loads(base64.b64decode(body['content']))
        if value.get('channel','').lower()!=common['channel'].lower():raise ValueError('Previous channel status')
        value={k:value.get(k) for k in ('checked_at','result','today_sent','last_post','sources')}
        value['available']=True
    except Exception:
        value={**(CACHE['value'] or {}),'available':False,'note':'Не удалось получить свежий статус облака. Это не подтверждение остановки публикаций.'}
    CACHE.update(until=time.monotonic()+180,value=value)
    return {**common,**value}
