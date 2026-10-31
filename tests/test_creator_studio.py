from datetime import date
from invest_blog.creator_studio import CreatorStore


def test_seed_is_repeatable_and_keeps_user_changes(tmp_path):
    store=CreatorStore(tmp_path/'creator.sqlite3')
    assert store.seed(date(2026,11,2))==12
    assert store.seed(date(2026,11,2))==0
    rows=store.list(date(2026,11,2))
    assert len(rows)==12
    assert [x['format'] for x in rows[:3]]==['project_log','carousel','maker_vibe']
    edited=store.update(rows[0]['id'],{'revision':0,'title':'Реальный прогресс шлема','status':'editing'})
    assert edited['revision']==1
    assert store.seed(date(2026,11,2))==0
    assert store.list(date(2026,11,2))[0]['title']=='Реальный прогресс шлема'
    assert store.update(rows[0]['id'],{'revision':0,'title':'Перезаписать'}) is None


def test_projects_and_validation(tmp_path):
    store=CreatorStore(tmp_path/'creator.sqlite3')
    store.seed(date(2026,11,2))
    rows=store.list(date(2026,11,2))
    assert {x['project'] for x in rows}=={'helmet','nova','spider'}
    import pytest
    with pytest.raises(ValueError):
        store.update(rows[0]['id'],{'revision':0,'status':'unknown'})
    with pytest.raises(ValueError):
        store.update(rows[0]['id'],{'revision':0,'title':''})
