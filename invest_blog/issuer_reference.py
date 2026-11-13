"""MOEX ISS issuer identity, exact ISIN matches only. Never receives broker credentials."""
from __future__ import annotations

import re

import requests

URL = 'https://iss.moex.com/iss/securities.json'


def issuer_key(raw):
    value = str(raw or '').strip()
    return 'inn:' + value if re.fullmatch(r'\d{10}|\d{12}', value) else value


class IssuerReference:
    def __init__(self, session=None):
        # This session MUST be separate from TInvest.session (Bearer header).
        self.session = session or requests.Session()

    def lookup(self, isin):
        if not isinstance(isin, str) or not re.fullmatch(r'[A-Z]{2}[A-Z0-9]{9}\d', isin):
            return None
        response = self.session.get(URL, params={'q': isin, 'iss.meta': 'off',
                                    'iss.only': 'securities',
                                    'securities.columns': 'isin,emitent_id,emitent_title,emitent_inn'}, timeout=12)
        response.raise_for_status()
        block = response.json().get('securities', {})
        columns, data = block.get('columns'), block.get('data')
        if not isinstance(columns, list) or not isinstance(data, list) or 'isin' not in columns:
            raise ValueError('MOEX: нет таблицы securities/isin')
        matches = []
        for values in data:
            if not isinstance(values, list) or len(values) != len(columns):
                continue
            row = dict(zip(columns, values))
            if row.get('isin') != isin:
                continue
            inn = str(row.get('emitent_inn') or '').strip()
            identity = issuer_key(inn) if re.fullmatch(r'\d{10}|\d{12}', inn) else ('moex:' + str(row['emitent_id']) if row.get('emitent_id') else None)
            if identity and isinstance(row.get('emitent_title'), str) and row['emitent_title'].strip():
                matches.append({'issuer_id': identity, 'issuer_name': row['emitent_title'].strip(),
                                'source_url': response.url, 'isin': isin})
        identities = {r['issuer_id'] for r in matches}
        return matches[0] if len(identities) == 1 else None
