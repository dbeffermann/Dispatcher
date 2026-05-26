"""
python manage.py import_yaml [--project <id>] [--scene <scene_id>] [--dir <dir>]
                             [--replace] [--dry-run]

Imports YAML scene files into the Studio DB.

This is the inverse of export_yaml. It is used for:
  - AI-assisted bulk edits (edit YAML, then re-import)
  - Restoring from a YAML backup
  - Migrating content between projects

Safety model:
  - By default (no --replace) beats/choices for the scene are REPLACED entirely.
    This ensures the DB mirrors the YAML file exactly after import.
  - Scene meta (title, channel, next, objective) is always upserted.
  - Scenes not present in the YAML are left untouched.
  - The project record, cases, assets, contacts, and vignettes are NOT modified
    by a scene import. Use --project-meta to also import project.yaml.

Data flow:
    Studio DB  ──export_yaml──▶  yaml/scenes/*.yaml  ──compile──▶  game.json
    Studio DB  ◀──import_yaml──  yaml/scenes/*.yaml

Run export_dispatcher_json after import to regenerate game.json:
    python manage.py import_yaml --scene dispatch_choice
    python manage.py export_dispatcher_json
"""
import shutil
import sys
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from dispatcher_authoring.models import (
    Beat,
    Case,
    Choice,
    ChoiceOption,
    CaseField,
    GameProject,
    OptionBeat,
    Scene,
    ToolSearch,
    Vignette,
)
from dispatcher_authoring.yaml_io import load_yaml_file


# ---------------------------------------------------------------------------
# Beat import helpers  (reused from import_dispatcher_json logic)
# ---------------------------------------------------------------------------

def _resolve_vignette(vig_id: str, project) -> 'Vignette | None':
    if not vig_id:
        return None
    return Vignette.objects.filter(project=project, vignette_id=vig_id).first()


def _resolve_case_field(field_id: str, scene: 'Scene') -> 'CaseField | None':
    if not field_id or not scene.case_id:
        return None
    return CaseField.objects.filter(case=scene.case, field_id=field_id).first()


def _resolve_tool_search(tool_id: str, project) -> 'ToolSearch | None':
    if not tool_id:
        return None
    try:
        from dispatcher_authoring.models import ToolSearch
        return ToolSearch.objects.filter(project=project, search_id=tool_id).first()
    except Exception:
        return None


def _import_beat_dict(beat_data: dict, order: int, *, scene=None, option=None, project=None):
    """Create a Beat or OptionBeat from a YAML beat dict."""
    kind = beat_data.get('kind', '')

    common = dict(
        order=order,
        kind=kind,
        text=beat_data.get('text', '') or '',
        speaker=beat_data.get('speaker', '') or '',
    )

    if kind == 'vignette':
        vig_id = beat_data.get('scene', '')
        common['vignette_action'] = beat_data.get('action', '') or ''
        common['vignette_ref'] = vig_id
        if project:
            vig_obj = _resolve_vignette(vig_id, project)
            if vig_obj:
                common['vignette'] = vig_obj

    elif kind == 'input':
        field_id = beat_data.get('field', '')
        common['input_field'] = field_id
        common['input_set_value'] = beat_data.get('setValue', '') or ''
        common['input_answer'] = beat_data.get('answer', '') or ''
        common['input_hint'] = beat_data.get('hint', '') or ''
        common['input_error_msg'] = beat_data.get('errorMsg', '') or ''
        if scene:
            cf = _resolve_case_field(field_id, scene)
            if cf:
                common['input_case_field'] = cf

    elif kind == 'tool-search':
        tool_id = beat_data.get('tool', '')
        common['tool_ref'] = tool_id
        if project:
            ts = _resolve_tool_search(tool_id, project)
            if ts:
                common['tool_search'] = ts

    elif kind == 'wa':
        common['wa_contact'] = beat_data.get('contact', 'dani') or 'dani'
        common['media_type'] = beat_data.get('mediaType', '') or ''
        common['media_url'] = beat_data.get('url', '') or ''
        common['media_caption'] = beat_data.get('caption', '') or ''
        common['media_name'] = beat_data.get('name', '') or ''
        common['media_duration'] = beat_data.get('dur', '') or ''

    elif kind == 'sfx':
        common['sfx_key'] = beat_data.get('sfx', '') or ''
        common['sfx_action'] = beat_data.get('sfxAction', 'play') or 'play'

    elif kind == 'ambient':
        common['ambient_key'] = beat_data.get('ambient', '') or ''
        common['ambient_stop'] = bool(beat_data.get('stop', False))

    elif kind == '':
        # Dialogue — may have media
        common['media_type'] = beat_data.get('mediaType', '') or ''
        common['media_url'] = beat_data.get('url', '') or ''
        common['media_caption'] = beat_data.get('caption', '') or ''
        common['media_name'] = beat_data.get('name', '') or ''
        common['media_duration'] = beat_data.get('dur', '') or ''

    if scene is not None:
        return Beat.objects.create(scene=scene, **common)
    if option is not None:
        return OptionBeat.objects.create(option=option, **common)


# ---------------------------------------------------------------------------
# Scene import
# ---------------------------------------------------------------------------

def _import_scene_yaml(data: dict, project, dry_run: bool, log) -> dict:
    """
    Upsert a scene from its YAML dict.
    Returns a summary dict: {scene_id, created, beats_imported, choices_imported}.
    """
    scene_id = (data.get('scene_id') or '').strip()
    if not scene_id:
        raise CommandError("Scene YAML missing 'scene_id' field.")

    title = data.get('title', '') or ''
    channel = data.get('channel', '') or ''
    objective = data.get('objective', '') or ''
    next_id = data.get('next', '') or ''
    case_id = data.get('case', '') or ''

    # Resolve FKs
    next_scene = Scene.objects.filter(project=project, scene_id=next_id).first() if next_id else None
    case = Case.objects.filter(project=project, case_id=case_id).first() if case_id else None

    if dry_run:
        log(f'  [dry-run] Would upsert scene: {scene_id!r} ({len(data.get("beats", []))} beats, '
            f'{len(data.get("choices", []))} choices)')
        return {'scene_id': scene_id, 'created': False, 'beats_imported': 0, 'choices_imported': 0}

    # Upsert scene
    scene, created = Scene.objects.update_or_create(
        project=project, scene_id=scene_id,
        defaults={
            'title': title,
            'channel': channel,
            'objective': objective,
            'next_scene': next_scene,
            'next_scene_str': next_id if not next_scene else '',
            'case': case,
        },
    )

    # Replace beats & choices entirely
    scene.beats.all().delete()
    for ch in scene.choices.all():
        ch.options.all().delete()
    scene.choices.all().delete()

    # Import beats
    beats_data = data.get('beats') or []
    for order, beat_data in enumerate(beats_data):
        if isinstance(beat_data, dict):
            _import_beat_dict(beat_data, order, scene=scene, project=project)

    # Import choices
    choices_data = data.get('choices') or []
    for ch_data in choices_data:
        if not isinstance(ch_data, dict):
            continue
        at = int(ch_data.get('at', 0) or 0)
        prompt = ch_data.get('prompt', '') or ''
        choice = Choice.objects.create(scene=scene, at_beat=at, prompt=prompt)
        for opt_order, opt_data in enumerate(ch_data.get('options') or []):
            if not isinstance(opt_data, dict):
                continue
            goto_id = opt_data.get('goto', '') or ''
            goto_scene = Scene.objects.filter(project=project, scene_id=goto_id).first() if goto_id else None
            opt = ChoiceOption.objects.create(
                choice=choice,
                order=opt_order,
                label=opt_data.get('label', '') or '',
                goto_scene=goto_scene,
                goto=goto_id if not goto_scene else '',
            )
            for ob_order, ob_data in enumerate(opt_data.get('beats') or []):
                if isinstance(ob_data, dict):
                    _import_beat_dict(ob_data, ob_order, option=opt, project=project)

    action = 'Created' if created else 'Updated'
    log(f'  {action}: {scene_id} ({len(beats_data)} beats, {len(choices_data)} choices)')
    return {
        'scene_id': scene_id,
        'created': created,
        'beats_imported': len(beats_data),
        'choices_imported': len(choices_data),
    }


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = 'Import YAML scene files → Studio DB (inverse of export_yaml)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--project', type=int, default=None,
            help='DB pk of the target project (default: first project)',
        )
        parser.add_argument(
            '--scene', type=str, default=None,
            help='Import only this scene_id from <dir>/scenes/<scene_id>.yaml',
        )
        parser.add_argument(
            '--dir', type=str, default=None,
            help='Directory containing project.yaml and scenes/ (default: <DISPATCHER_DATA_DIR>/../yaml)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Preview what would be imported without writing to DB',
        )

    def handle(self, *args, **options):
        # Resolve project
        if options['project']:
            project = GameProject.objects.get(pk=options['project'])
        else:
            project = GameProject.objects.first()
        if not project:
            raise CommandError('No projects found. Run import_dispatcher_json first.')

        # Resolve directory
        if options['dir']:
            yaml_dir = Path(options['dir'])
        else:
            data_dir = Path(getattr(settings, 'DISPATCHER_DATA_DIR',
                                    Path(__file__).resolve().parents[5] / 'dispatcher_cases_v2' / 'data'))
            yaml_dir = data_dir.parent / 'yaml'

        scenes_dir = yaml_dir / 'scenes'
        if not scenes_dir.exists():
            raise CommandError(f'Scenes directory not found: {scenes_dir}')

        dry_run = options['dry_run']

        def log(msg):
            self.stdout.write(msg)

        # Resolve which files to import
        if options['scene']:
            yaml_files = [scenes_dir / f"{options['scene']}.yaml"]
            for f in yaml_files:
                if not f.exists():
                    raise CommandError(f'Scene file not found: {f}')
        else:
            yaml_files = sorted(scenes_dir.glob('*.yaml'))
            if not yaml_files:
                raise CommandError(f'No .yaml files found in {scenes_dir}')

        log(f'Importing {len(yaml_files)} scene(s) into project "{project.name}" '
            f'{"[DRY RUN]" if dry_run else ""}')

        # ------------------------------------------------------------------
        # Auto-backup the SQLite DB before any real write
        # ------------------------------------------------------------------
        if not dry_run:
            db_path = Path(settings.DATABASES['default']['NAME'])
            if db_path.exists():
                backup_dir = db_path.parent / 'db_backups'
                backup_dir.mkdir(exist_ok=True)
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                backup_path = backup_dir / f'{ts}_before_import_yaml.sqlite3'
                shutil.copy2(db_path, backup_path)
                log(f'Backup: {backup_path.name}')

        total = {'created': 0, 'updated': 0, 'beats': 0, 'choices': 0}

        with transaction.atomic():
            for path in yaml_files:
                try:
                    data = load_yaml_file(path)
                except ValueError as e:
                    raise CommandError(str(e))
                if not isinstance(data, dict):
                    log(f'  SKIP {path.name}: not a mapping')
                    continue
                result = _import_scene_yaml(data, project, dry_run, log)
                if result['created']:
                    total['created'] += 1
                else:
                    total['updated'] += 1
                total['beats'] += result['beats_imported']
                total['choices'] += result['choices_imported']

            if dry_run:
                # Roll back even in dry-run so DB is not touched
                from django.db import connection
                transaction.set_rollback(True)

        summary = (
            f"\nDone: {total['created']} created, {total['updated']} updated | "
            f"{total['beats']} beats, {total['choices']} choices"
        )
        if dry_run:
            summary += '  [DRY RUN — no changes written]'
        else:
            summary += '\nRun: python manage.py export_dispatcher_json  (to recompile game.json)'

        self.stdout.write(self.style.SUCCESS(summary))
