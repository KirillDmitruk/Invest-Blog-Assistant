from datetime import date,timedelta,timezone,datetime
import pytest
from invest_blog.investment_lessons import THEMES,ranked,build

def test_each_theme_has_company_examples_and_no_live_company_numbers():
    assert len(THEMES)>=12 and len({t['id'] for t in THEMES})==len(THEMES)
    for theme in THEMES:
        row=build(date(2026,11,27),topic=theme['title'],use_ai=False)
        assert len(row['body'])<3900,(theme['id'],len(row['body']))
        assert 'Числовые примеры выше условные' in row['body']
        assert all(ticker in row['body'] for name,ticker,note in theme['companies'])
        assert '[' not in row['body'] and 'Что это такое?' not in row['body']

def test_topics_follow_news_context_and_do_not_repeat_recent_posts():
    first=ranked((), 'денежный поток прибыль выручка отчётность инвестиции',date(2026,11,27))[0]
    assert first['id']=='cash'
    rows=ranked((first['title'],), 'денежный поток прибыль',date(2026,11,27))
    assert rows[0]['id']!='cash'
    recent=tuple(t['title'] for t in THEMES)
    assert ranked(recent,'',date(2026,11,27))[0]['title']==recent[-1]

def test_ai_cannot_invent_topic_numbers_or_company_metrics(monkeypatch):
    import invest_blog.investment_lessons as m
    monkeypatch.setenv('CF_ACCOUNT_ID','fake');monkeypatch.setenv('CF_AI_TOKEN','secret')
    monkeypatch.setattr(m,'model_json',lambda *args,**kwargs:{'id':'unknown','text':'У Сбера ROE 999%.'*90})
    row=build(date(2026,11,27),context='')
    assert row['status']=='curated' and '999' not in row['body']
    assert row['suggestions']

def test_verified_edit_is_used_but_company_examples_stay_in_code(monkeypatch):
    import invest_blog.investment_lessons as m
    monkeypatch.setenv('CF_ACCOUNT_ID','fake');monkeypatch.setenv('CF_AI_TOKEN','secret')
    prose=('Прибыль и движение денег нужно рассматривать вместе. Условный пример помогает разобраться, но не описывает показатели реальной компании. '*10)
    monkeypatch.setattr(m,'model_json',lambda *a,**kw:{'text':prose})
    row=build(date(2026,11,27),topic=THEMES[1]['title'],context='')
    assert row['status']=='ai_edited' and prose in row['body'] and '$YDEX' in row['body']

def test_weekly_migration_preserves_selected_topics_and_linked_drafts(tmp_path,monkeypatch):
    from invest_blog.editorial_workflow import EditorialWorkflow
    from invest_blog.storage import DraftStore
    p=tmp_path/'test.db';DraftStore(p);workflow=EditorialWorkflow(p)
    monkeypatch.setattr(workflow,'_find_online_titles',lambda:('https://example.org','прибыль денежный поток'))
    with workflow.connect() as db:
        db.execute("INSERT INTO editorial_glossary_ideas(topic,status,week) VALUES('Моя выбранная тема','selected','2026-11-23')")
        db.execute("INSERT INTO editorial_glossary_ideas(topic,status,week) VALUES('Старая нетронутая тема','reserve','2026-11-23')")
        db.execute("INSERT INTO editorial_idea_refresh VALUES('2026-11-23','old','offline_fallback')")
    ideas=workflow.refresh_ideas(date(2026,11,27))
    assert len(ideas)==3 and all(x['topic'] in {t['title'] for t in THEMES} for x in ideas)
    assert workflow.selection(date(2026,11,27))=='Моя выбранная тема'
