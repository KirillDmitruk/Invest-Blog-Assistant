"""Offline, explicit export of Walt-only state. No network or broker methods."""
import argparse,json,os,sqlite3,sys,time
from pathlib import Path
from cryptography.fernet import Fernet
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from invest_blog.walt_agent import protect

TABLES=('walt_agent_state','walt_trade_limits','walt_trade_signals','walt_trade_drafts','walt_wallet_snapshot')

def snapshot(source,key,decrypt=protect):
    cipher=Fernet(key)
    # Read one consistent SQLite snapshot; never export the complete hub DB.
    with sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro',uri=True) as original:
        memory=sqlite3.connect(':memory:');original.backup(memory)
    try:
        for table in ('walt_agent_lock','walt_trade_lock'):
            if memory.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():
                if memory.execute('SELECT 1 FROM '+table+' WHERE expires>?',(time.time(),)).fetchone():raise ValueError('Операция Walt ещё выполняется. Останови хаб и дождись завершения.')
        if memory.execute("SELECT 1 FROM walt_trade_drafts WHERE status IN ('executing','uncertain','submitted')").fetchone():
            raise ValueError('Есть неподтверждённая отправка. Сначала сверить её в Walt; перенос остановлен.')
        target=sqlite3.connect(':memory:')
        try:
            for table in TABLES:
                schema=memory.execute('SELECT sql FROM sqlite_master WHERE type=\'table\' AND name=?',(table,)).fetchone()
                if not schema:continue
                target.execute(schema[0])
                rows=memory.execute('SELECT * FROM '+table).fetchall()
                if rows:target.executemany('INSERT INTO '+table+' VALUES('+','.join('?' for _ in rows[0])+')',rows)
            target.execute('CREATE TABLE walt_agent_secrets(name TEXT PRIMARY KEY,value BLOB)')
            for name in ('client','tokens'):
                row=memory.execute('SELECT value FROM walt_agent_secrets WHERE name=?',(name,)).fetchone()
                if not row:raise ValueError('Существующее подключение Walt не найдено.')
                plain=decrypt(bytes(row[0]),True);value=json.loads(plain)
                if name=='tokens' and 'perps.trade' not in value.get('scope','').split():raise ValueError('Торговые права Walt не подтверждены.')
                target.execute('INSERT INTO walt_agent_secrets VALUES(?,?)',(name,cipher.encrypt(plain)))
            target.execute("UPDATE walt_trade_drafts SET status='expired' WHERE status IN ('previewed','calculated')")
            target.commit();plain=target.serialize()
            if len(plain)>8_000_000:raise ValueError('Состояние слишком велико; требуется очистка истории перед переносом.')
            return cipher.encrypt(plain)
        finally:target.close()
    finally:memory.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--confirm-cloud-transfer',action='store_true');args=parser.parse_args()
    if not args.confirm_cloud_transfer:raise SystemExit('Нужно отдельное согласие на перенос торговой авторизации Walt.')
    output=Path(args.output)
    if not output.name.endswith('.walt-state.enc'):raise SystemExit('Допускается только зашифрованный файл *.walt-state.enc.')
    try:
        encrypted=snapshot(args.database,os.environ['WALT_CLOUD_STATE_KEY'].encode())
        with output.open('xb') as handle:handle.write(encrypted)
        print('Encrypted Walt-only snapshot created. No orders sent; keys withheld.')
    except Exception:raise SystemExit('Transfer stopped. Details and secrets withheld; no orders sent.') from None

if __name__=='__main__':main()
