"""Delete only known outdated generated Markdown documents from project root.

Unknown/custom Markdown files and docs inside subdirectories are never touched.
By default previews deletions; --apply is required to remove files.
"""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD_DOCS = frozenset({
    'PATCH_NOTES.md', 'V1_1_README.md', 'V1_2_README.md', 'V1_3_README.md',
    'HISTORY_PATCH_README.md', 'HISTORY_QUANTITY_FIX.md',
    'CURRENCY_FIX_README.md', 'POINTS_QUOTE_FIX_README.md',
    'BOND_NOMINAL_FIX_README.md', 'REDEMPTION_FIX_README.md',
    'BOND_EVENTS_FIX_README.md', 'ANCHOR_FIX_README.md',
    'DEDUP_FIX_README.md', 'ETF_UID_FIX_README.md',
})
KEEP = frozenset({'README.md','ROADMAP.md','TEMPLATES.md'})


def cleanup(root: Path = ROOT, *, apply: bool = False) -> list[str]:
    found=[]
    for name in sorted(OLD_DOCS):
        path=root / name
        if path.is_file() and not path.is_symlink():
            found.append(name)
            if apply:
                path.unlink()
    return found


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Удалить перечисленные файлы')
    args = parser.parse_args()
    files=cleanup(apply=args.apply)
    action='Удалены' if args.apply else 'Будут удалены (для удаления: --apply)'
    print(f'{action}: '+(', '.join(files) if files else 'ничего'))
    print('Сохранены: '+', '.join(sorted(KEEP)))
