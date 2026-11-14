"""Create a local connector credential. Never output it or replace existing secrets."""
from pathlib import Path
import secrets
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
path = root / '.env'
values = dotenv_values(path)
if len(values.get('GROK_CONNECTOR_TOKEN') or '') >= 32:
    print('Connector key already exists in local .env. No changes.')
else:
    text = path.read_text(encoding='utf-8-sig') if path.exists() else ''
    lines = [line for line in text.splitlines() if not line.startswith('GROK_CONNECTOR_TOKEN=')]
    lines.append('GROK_CONNECTOR_TOKEN='+secrets.token_urlsafe(48))
    with path.open('w', encoding='utf-8') as file:
        file.write('\n'.join(lines)+'\n')
    print('Created GROK_CONNECTOR_TOKEN in local .env. It is NOT an xAI or broker key. Keep it private.')
