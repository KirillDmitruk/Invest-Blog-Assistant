from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from invest_blog.tech_news import TechNewsStore
from invest_blog.vk_news import VKNewsQueue, run, compose

def test_preview_and_no_send(tmp_path):
    p=tmp_path/'test.sqlite3'; store=TechNewsStore(p)
    store.save([{'source':'Example','title':'New robot announced','url':'https://example.com/robot','summary':'A company announced a new robotic device that works in factories and has improved efficiency.','category':'robotics','published_at':datetime.now(timezone.utc).isoformat()}])
    translate=lambda x:'Новый робот представлен разработчиками' if 'robot announced' in x else 'Компания представила нового робота для работы на производстве и сообщила об улучшении его производительности'
    result=run(p,ZoneInfo('Europe/Moscow'),limit=4,translator=translate)
    assert len(result['prepared'])==1
    assert VKNewsQueue(p).count(result['day'])==0

def test_reserve_dedup_limit(tmp_path):
    q=VKNewsQueue(tmp_path/'test.sqlite3')
    for i in range(1,5): assert q.reserve({'id':i},'2026-10-31','test')
    assert not q.reserve({'id':1},'2026-10-31','test')
    assert not q.reserve({'id':5},'2026-10-31','test')
    assert q.count('2026-10-31')==4

def test_bad_summary():
    try:compose({'title':'Title','summary':'','source':'x','url':'https://x'},lambda x:x)
    except ValueError: pass
    else: assert False
