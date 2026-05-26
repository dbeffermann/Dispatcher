"""
validate_roundtrip.py — Verifica que export_yaml + import_yaml es lossless.

Workflow:
  1. Snapshot game.json actual
  2. Exporta DB -> YAML (regenera todos los .yaml)
  3. Importa YAML -> DB (aplica con backup automático)
  4. Recompila DB -> game.json
  5. Compara JSON antes/después: reporta diffs campo a campo
  6. Valida YAML contra el perfil del juego (validate_yaml)
  7. (--test-restore) Restaura desde backup y verifica identidad

Uso:
  cd authoring/
  python validate_roundtrip.py

  # Solo comparar sin re-importar:
  python validate_roundtrip.py --compare-only

  # Usar YAML ya exportados (no re-exportar):
  python validate_roundtrip.py --skip-export

  # Probar restauracion desde backup:
  python validate_roundtrip.py --test-restore

  # Validacion completa (roundtrip + restore + validate_yaml):
  python validate_roundtrip.py --full
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (relativo a authoring/)
# ---------------------------------------------------------------------------
HERE = Path(__file__).parent
ROOT = HERE.parent  # dispatcher_cases_v2/ root

DATA_DIR = ROOT / 'dispatcher_cases_v2' / 'data'
STORY_JSON = DATA_DIR / 'dispatcher_story.json'
DB_PATH = HERE / 'db.sqlite3'
BACKUPS_DIR = HERE / 'db_backups'
MANAGE = HERE / 'manage.py'
PYTHON = sys.executable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(cmd: list[str], label: str):
    print(f'\n>>> {label}')
    result = subprocess.run(
        [PYTHON] + cmd,
        cwd=str(HERE),
        capture_output=True,
        text=True,
        encoding='utf-8',
    )
    for line in result.stdout.strip().splitlines():
        print(f'    {line}')
    if result.returncode != 0:
        for line in result.stderr.strip().splitlines():
            print(f'    ERR: {line}')
        raise SystemExit(f'Command failed: {" ".join(cmd)}')
    return result.stdout


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


# ---------------------------------------------------------------------------
# Deep diff between two objects — returns list of (path, before, after)
# ---------------------------------------------------------------------------

def _diff(a, b, path=''):
    diffs = []
    if type(a) != type(b):
        diffs.append((path, repr(a)[:120], repr(b)[:120]))
        return diffs
    if isinstance(a, dict):
        all_keys = set(a) | set(b)
        for k in sorted(all_keys):
            child = f'{path}.{k}' if path else k
            if k not in a:
                diffs.append((child, '<missing>', repr(b[k])[:120]))
            elif k not in b:
                diffs.append((child, repr(a[k])[:120], '<missing>'))
            else:
                diffs.extend(_diff(a[k], b[k], child))
    elif isinstance(a, list):
        if len(a) != len(b):
            diffs.append((f'{path}[len]', str(len(a)), str(len(b))))
        for i, (x, y) in enumerate(zip(a, b)):
            diffs.extend(_diff(x, y, f'{path}[{i}]'))
    elif a != b:
        diffs.append((path, repr(a)[:120], repr(b)[:120]))
    return diffs


def test_restore() -> bool:
    """
    Verifica que restaurar el backup mas reciente produce un JSON identico.

    Logica:
      1. Toma una copia de db.sqlite3 actual ('current')
      2. Encuentra el backup mas reciente en db_backups/
      3. Copia el backup sobre db.sqlite3
      4. Ejecuta export_dispatcher_json
      5. Compara la salida con el JSON de referencia actual
      6. Restaura db.sqlite3 original (cleanup garantizado)

    Pre-condicion: ya se ejecuto un roundtrip en esta sesion, o existe
                   al menos un backup previo en db_backups/.
    """
    print('\n' + '=' * 60)
    print('TEST: BACKUP RESTORE')
    print('=' * 60)

    if not BACKUPS_DIR.exists():
        print('  SKIP: db_backups/ not found. Run a roundtrip first to create backups.')
        return True  # no error, just skip

    backups = sorted(BACKUPS_DIR.glob('*_before_import_yaml.sqlite3'))
    if not backups:
        print('  SKIP: No backups found in db_backups/.')
        return True

    latest_backup = backups[-1]
    print(f'  Using backup : {latest_backup.name}')

    # 1. Snapshot reference JSON (export from current DB)
    ref_json_path = DATA_DIR / '_snapshots' / '_restore_ref.json'
    ref_json_path.parent.mkdir(exist_ok=True)
    run(['manage.py', 'export_dispatcher_json'], 'Snapshot: export current DB -> game.json')
    ref_json = load_json(STORY_JSON)
    ref_json_path.write_text(
        json.dumps(ref_json, ensure_ascii=False, indent=2), encoding='utf-8'
    )

    # 2. Save current DB
    current_db_copy = HERE / '_current_db_for_restore_test.sqlite3'
    shutil.copy2(DB_PATH, current_db_copy)
    print(f'  DB saved     : {current_db_copy.name}')

    ok = False
    try:
        # 3. Restore from backup
        shutil.copy2(latest_backup, DB_PATH)
        print(f'  DB restored  : {latest_backup.name} -> db.sqlite3')

        # 4. Recompile from restored DB
        run(['manage.py', 'export_dispatcher_json'],
            'Recompile from restored DB -> game.json')
        restored_json = load_json(STORY_JSON)

        # 5. Compare
        print('\n--- Comparing reference vs restored ---')
        n = compare_stories(ref_json, restored_json)
        if n == 0:
            print('\n  RESTORE TEST: PASS — restored DB produces identical JSON')
            ok = True
        else:
            print(f'\n  RESTORE TEST: FAIL — {n} difference(s) after restore')
    finally:
        # 6. Always restore original DB
        shutil.copy2(current_db_copy, DB_PATH)
        current_db_copy.unlink(missing_ok=True)
        # Recompile again to leave game.json in correct state
        run(['manage.py', 'export_dispatcher_json'],
            'Cleanup: restore original DB and recompile game.json')
        print('  DB restored to original state.')

    return ok


def run_validate_yaml() -> bool:
    """Run validate_yaml against all scenes and return True if passed."""
    print('\n' + '=' * 60)
    print('VALIDATE YAML (all scenes vs. dispatcher profile)')
    print('=' * 60)
    result = subprocess.run(
        [PYTHON, 'manage.py', 'validate_yaml'],
        cwd=str(HERE),
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    stdout = result.stdout or ''
    for line in stdout.strip().splitlines():
        # Strip non-ASCII to avoid cp1252 issues with accented scene titles
        safe = line.encode('ascii', errors='replace').decode('ascii')
        print(f'    {safe}')
    if result.returncode != 0:
        stderr = result.stderr or ''
        for line in stderr.strip().splitlines():
            print(f'    ERR: {line}')
        return False
    return True


def compare_stories(before: dict, after: dict) -> int:
    """Compare two story dicts; print diffs. Returns count of differences."""
    print('\n=== JSON COMPARISON ===')

    # Scene-by-scene comparison
    scenes_b = {s['id']: s for s in before.get('scenes', [])}
    scenes_a = {s['id']: s for s in after.get('scenes', [])}

    all_ids = sorted(set(scenes_b) | set(scenes_a))
    total_diffs = 0

    for sid in all_ids:
        if sid not in scenes_b:
            print(f'  [NEW]     {sid}')
            total_diffs += 1
            continue
        if sid not in scenes_a:
            print(f'  [REMOVED] {sid}')
            total_diffs += 1
            continue
        diffs = _diff(scenes_b[sid], scenes_a[sid], sid)
        if diffs:
            print(f'\n  [CHANGED] {sid}:')
            for path, bval, aval in diffs:
                print(f'    {path}')
                print(f'      before: {bval}')
                print(f'      after:  {aval}')
            total_diffs += len(diffs)
        else:
            print(f'  [OK]      {sid}')

    # Top-level diff (non-scenes)
    top_b = {k: v for k, v in before.items() if k != 'scenes'}
    top_a = {k: v for k, v in after.items() if k != 'scenes'}
    top_diffs = _diff(top_b, top_a, '<root>')
    if top_diffs:
        print('\n  [CHANGED] top-level (non-scenes):')
        for path, bval, aval in top_diffs:
            print(f'    {path}: {bval!r} -> {aval!r}')
        total_diffs += len(top_diffs)

    return total_diffs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--compare-only', action='store_true',
                        help='Skip roundtrip; only compare the two most recent JSON snapshots')
    parser.add_argument('--skip-export', action='store_true',
                        help='Use existing YAML files (skip export_yaml step)')
    parser.add_argument('--test-restore', action='store_true',
                        help='Also test that restoring the latest DB backup produces identical JSON')
    parser.add_argument('--full', action='store_true',
                        help='Full validation: roundtrip + validate_yaml + restore test')
    args = parser.parse_args()

    if args.full:
        args.test_restore = True

    print('=' * 60)
    print('ROUNDTRIP VALIDATION')
    print('=' * 60)

    if args.compare_only:
        # Find two snapshots
        snap_dir = DATA_DIR / '_snapshots'
        snaps = sorted(snap_dir.glob('dispatcher_story_*.json'))
        if len(snaps) < 2:
            raise SystemExit('Need at least 2 snapshots. Run without --compare-only first.')
        before = load_json(snaps[-2])
        after = load_json(snaps[-1])
        print(f'Comparing:\n  before: {snaps[-2].name}\n  after:  {snaps[-1].name}')
        n = compare_stories(before, after)
    else:
        # Step 1: snapshot current game.json
        snap_dir = DATA_DIR / '_snapshots'
        snap_dir.mkdir(exist_ok=True)
        from datetime import datetime
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        snap_before = snap_dir / f'dispatcher_story_{ts}_before.json'
        before = load_json(STORY_JSON)
        snap_before.write_text(
            json.dumps(before, ensure_ascii=False, indent=2), encoding='utf-8'
        )
        print(f'\nSnapshot before: {snap_before.name}')

        # Step 2: export YAML
        if not args.skip_export:
            run(['manage.py', 'export_yaml'], 'export_yaml: DB -> YAML')

        # Step 3: import YAML (auto-backups DB)
        run(['manage.py', 'import_yaml'], 'import_yaml: YAML -> DB  [auto-backup created]')

        # Step 4: recompile game.json
        run(['manage.py', 'export_dispatcher_json'], 'export_dispatcher_json: DB -> game.json')

        # Step 5: compare
        after = load_json(STORY_JSON)
        snap_after = snap_dir / f'dispatcher_story_{ts}_after.json'
        snap_after.write_text(
            json.dumps(after, ensure_ascii=False, indent=2), encoding='utf-8'
        )
        print(f'Snapshot after:  {snap_after.name}')

        n = compare_stories(before, after)

    print('\n' + '=' * 60)
    if n == 0:
        print('RESULT: PASS — roundtrip is lossless (0 differences)')
    else:
        print(f'RESULT: {n} difference(s) found — review above')
    print('=' * 60)

    # Step 6: validate YAML content against game profile
    yaml_ok = run_validate_yaml()

    # Step 7: (optional) backup restore test
    restore_ok = True
    if args.test_restore:
        restore_ok = test_restore()

    overall_pass = (n == 0) and yaml_ok and restore_ok
    print('\n' + '=' * 60)
    print(f'OVERALL: {"PASS" if overall_pass else "FAIL"}')
    if n != 0:
        print(f'  Roundtrip diffs   : {n}')
    if not yaml_ok:
        print('  validate_yaml     : FAIL')
    if not restore_ok:
        print('  Backup restore    : FAIL')
    print('=' * 60)
    sys.exit(0 if overall_pass else 1)


if __name__ == '__main__':
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'authoring_project.settings')
    import django
    django.setup()
    main()
