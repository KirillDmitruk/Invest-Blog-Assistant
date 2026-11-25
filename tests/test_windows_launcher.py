"""Windows launcher forwards arguments without starting services or publishing."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which('powershell.exe')
pytestmark = pytest.mark.skipif(os.name != 'nt' or not POWERSHELL,
                                reason='Windows PowerShell launcher')


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


@pytest.mark.parametrize(('arguments', 'expected'), [
    (['web', '--port', '8080'], ['run', 'python', '-m', 'invest_blog.cli', 'web', '--port', '8080']),
    (['bot'], ['run', 'python', '-m', 'invest_blog.cli', 'bot']),
    (['trading-worker'], ['run', 'python', '-m', 'invest_blog.cli', 'trading-worker']),
    (['grok-connector', '--port', '8769'], ['run', 'python', '-m', 'invest_blog.cli', 'grok-connector', '--port', '8769']),
    (['grok-key'], ['run', 'python', 'tools/setup_grok_connector.py']),
    (['tech-publisher'], ['run', 'python', '-m', 'invest_blog.cli', 'tech-publisher']),
    (['translation-model'], ['run', '--extra', 'translation', 'python', 'tools/setup_translation.py']),
    (['install', '--python', '3.13'], ['sync', '--extra', 'dev', '--python', '3.13']),
    (['test', '-k', 'editorial or backup'], ['run', '--extra', 'dev', 'python', '-m', 'pytest', '-q', '-k', 'editorial or backup']),
    (['backup', '--db', r'C:\My Data\drafts.sqlite3'], ['run', 'python', 'tools/backup_db.py', '--db', r'C:\My Data\drafts.sqlite3']),
    (['weekly', '--date', '2026-11-13'], ['run', 'python', '-m', 'invest_blog.cli', 'preview', 'weekly', '--date', '2026-11-13']),
    (['--translation', 'web'], ['run', '--extra', 'translation', 'python', '-m', 'invest_blog.cli', 'web']),
    (['--translation', 'install', '--python', '3.13'], ['sync', '--extra', 'dev', '--extra', 'translation', '--python', '3.13']),
    (['vk-news', '--limit', '1'], ['run', 'python', '-m', 'invest_blog.cli', 'vk-news', '--limit', '1']),
])
def test_forwarding_and_project_directory(arguments, expected, tmp_path):
    # A function intercepts uv so tests cannot install packages, use tokens or send posts.
    script = """
function global:uv {
    [PSCustomObject]@{arguments=@($args); directory=(Get-Location).Path} | ConvertTo-Json -Compress
    $global:LASTEXITCODE = 7
}
""" + '& ' + ps_quote(ROOT / 'tools' / 'hub.ps1') + ' ' + ' '.join(map(ps_quote, arguments)) + '; exit $LASTEXITCODE'
    result = subprocess.run([POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', script],
                            cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 7, result.stderr
    data = json.loads(result.stdout)
    assert data['arguments'] == expected
    assert Path(data['directory']) == ROOT


def test_cmd_help_works_from_another_directory(tmp_path):
    result = subprocess.run([os.environ.get('COMSPEC', 'cmd.exe'), '/c', str(ROOT / 'hub.cmd'), 'help'],
                            cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'Content Hub' in result.stdout
    assert '.\\hub web' in result.stdout
