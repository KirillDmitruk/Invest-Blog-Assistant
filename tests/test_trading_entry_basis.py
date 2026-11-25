import json,sqlite3
import pytest
from invest_blog.trading_entry_basis import apply_entry_bases

@pytest.fixture
def db():
    d=sqlite3.connect(':memory:');d.execute('CREATE TABLE lab_state(id INTEGER,payload TEXT)');d.execute('CREATE TABLE lab_trades(id INTEGER,uid TEXT,at TEXT,action TEXT,payload TEXT)')
    d.execute('INSERT INTO lab_state VALUES(1,?)',(json.dumps({'budget':'1000000','cash':'1000000','positions':{}}),))
    for ident,at,action,detail in [(1,'2026-11-23T10:00:00Z','open_long',{'price':'100','contracts':2,'side':'long','fee':'4'}),(2,'2026-11-23T11:00:00Z','close',{'price':'110','contracts':2,'side':'long','fee':'4','pnl_before_exit_fee':'100'})]:
        d.execute('INSERT INTO lab_trades VALUES(?,?,?,?,?)',(ident,'uid',at,action,json.dumps(detail)))
    yield d;d.close()

def bundle():return {'version':1,'kind':'tinvest-futures-entry-bases','budget':'1000000','entries':{'1':{'uid':'uid','at':'2026-11-23T10:00:00Z','action':'open_long','price':'100','contracts':2,'side':'long','entry_margin':'1250.25','source_commit':'a'*40}}}
def payload(db,i):return json.loads(db.execute('SELECT payload FROM lab_trades WHERE id=?',(i,)).fetchone()[0])
def test_restores_original_margin_and_fees_without_rewriting_results_or_cash(db):
    before=db.execute('SELECT payload FROM lab_state').fetchone()[0]
    assert apply_entry_bases(db,bundle())==2
    assert payload(db,1)['entry_margin']==payload(db,2)['entry_margin']=='1250.25'
    assert payload(db,2)['entry_fee']=='4' and payload(db,2)['pnl_before_exit_fee']=='100'
    assert db.execute('SELECT payload FROM lab_state').fetchone()[0]==before
    assert apply_entry_bases(db,bundle())==0
@pytest.mark.parametrize('field,value',[('uid','other'),('price','101'),('at','2026-11-24T10:00:00Z'),('contracts',3),('source_commit','invalid'),('entry_margin','-100')])
def test_mismatched_or_invalid_archive_is_not_used(db,field,value):
    b=bundle();b['entries']['1'][field]=value
    assert apply_entry_bases(db,b)==0
    assert 'entry_margin' not in payload(db,2)
def test_existing_recorded_margin_is_not_overridden(db):
    p=payload(db,1);p['entry_margin']='2000';db.execute('UPDATE lab_trades SET payload=? WHERE id=1',(json.dumps(p),))
    apply_entry_bases(db,bundle());assert payload(db,1)['entry_margin']=='2000' and payload(db,2)['entry_margin']=='2000'
