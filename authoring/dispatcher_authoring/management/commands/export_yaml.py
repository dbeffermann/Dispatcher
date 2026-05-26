"""
python manage.py export_yaml [--project <id>] [--scene <scene_id>] [--out <dir>] [--dry-run]

Exports the Studio DB to YAML files — one file per scene — plus a project.yaml.

Output structure:
    <out>/
        project.yaml
        scenes/
            bienvenida.yaml
            intro.yaml
            dispatch_choice.yaml
            ...

YAML format is canonical: matches scene.schema.yaml and can be re-imported
with 'import_yaml'. It is intentionally close to the game JSON beat format
so that import/export roundtrips are lossless.

Data flow:
    Studio DB  ──export_yaml──▶  yaml/scenes/*.yaml  ──compile──▶  game.json
    Studio DB  ◀──import_yaml──  yaml/scenes/*.yaml

The YAML layer is for: git versioning, human review, AI editing, and backup.
It is NOT the source of truth — the DB is.
"""
from pathlib import Path
import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from dispatcher_authoring.models import GameProject


# ---------------------------------------------------------------------------
# Beat → YAML dict
# ---------------------------------------------------------------------------

def _beat_to_yaml(beat) -> dict:
    """Serialize a Beat/OptionBeat model to a clean YAML-friendly dict.

    Uses the same field names as the game JSON (kind, speaker, text, scene, ...)
    so the format is easy to read and losslessly round-trips through import_yaml.
    """
    kind = beat.kind  # '' = dialogue

    if kind == beat.NARRATION:
        return {'kind': 'narration', 'text': beat.text}

    if kind == beat.VIGNETTE:
        d = {'kind': 'vignette'}
        if beat.vignette_action:
            d['action'] = beat.vignette_action
        vig_id = beat.vignette.vignette_id if beat.vignette_id else beat.vignette_ref
        if vig_id:
            d['scene'] = vig_id
        if beat.text and beat.vignette_action in ('text', 'open', ''):
            d['text'] = beat.text
        return d

    if kind == beat.DISPATCH:
        return {'kind': 'dispatch'}

    if kind == beat.INPUT:
        field_id = beat.input_case_field.field_id if beat.input_case_field_id else beat.input_field
        d = {'kind': 'input', 'field': field_id}
        if beat.input_set_value:
            d['setValue'] = beat.input_set_value
        if beat.input_answer:
            d['answer'] = beat.input_answer
        if beat.input_hint:
            d['hint'] = beat.input_hint
        if beat.input_error_msg:
            d['errorMsg'] = beat.input_error_msg
        return d

    if kind == beat.TOOL_SEARCH:
        tool_id = beat.tool_search.search_id if beat.tool_search_id else beat.tool_ref
        return {'kind': 'tool-search', 'tool': tool_id}

    if kind == beat.WA:
        d = {
            'kind': 'wa',
            'contact': beat.wa_contact or 'dani',
            'text': beat.text,
        }
        if beat.speaker:
            d['speaker'] = beat.speaker
        if beat.media_type:
            d['mediaType'] = beat.media_type
        if beat.media_url:
            d['url'] = beat.media_url
        if beat.media_caption:
            d['caption'] = beat.media_caption
        if beat.media_name:
            d['name'] = beat.media_name
        if beat.media_duration:
            d['dur'] = beat.media_duration
        return d

    if kind == beat.SFX:
        action = beat.sfx_action or 'play'
        d = {'kind': 'sfx'}
        if action != 'play':
            d['sfxAction'] = action
        if action != 'stop':
            d['sfx'] = beat.sfx_key or ''
        return d

    if kind == beat.AMBIENT:
        d = {'kind': 'ambient'}
        if beat.ambient_stop:
            d['stop'] = True
        elif beat.ambient_key:
            d['ambient'] = beat.ambient_key
        return d

    # Dialogue (kind == '')
    d = {'speaker': beat.speaker, 'text': beat.text}
    if beat.media_type:
        d['mediaType'] = beat.media_type
    if beat.media_url:
        d['url'] = beat.media_url
    if beat.media_caption:
        d['caption'] = beat.media_caption
    if beat.media_name:
        d['name'] = beat.media_name
    if beat.media_duration:
        d['dur'] = beat.media_duration
    return d


# ---------------------------------------------------------------------------
# Scene → YAML dict
# ---------------------------------------------------------------------------

def _scene_to_yaml(scene) -> dict:
    """Serialize a Scene model to the canonical YAML dict."""
    d = {
        'scene_id': scene.scene_id,
        'title': scene.title,
    }

    if scene.channel:
        d['channel'] = scene.channel

    next_id = scene.next_scene.scene_id if scene.next_scene_id else scene.next_scene_str
    if next_id:
        d['next'] = next_id

    if scene.objective:
        d['objective'] = scene.objective

    if scene.case_id:
        d['case'] = scene.case.case_id

    beats = list(scene.beats.order_by('order'))
    if beats:
        d['beats'] = [_beat_to_yaml(b) for b in beats]

    choices = list(scene.choices.order_by('at_beat'))
    if choices:
        choices_out = []
        for ch in choices:
            opts = []
            for opt in ch.options.order_by('order'):
                opt_d = {'label': opt.label}
                goto = opt.goto_scene.scene_id if opt.goto_scene_id else opt.goto
                if goto:
                    opt_d['goto'] = goto
                opt_beats = list(opt.beats.order_by('order'))
                if opt_beats:
                    opt_d['beats'] = [_beat_to_yaml(b) for b in opt_beats]
                opts.append(opt_d)
            choices_out.append({
                'at': ch.at_beat,
                'prompt': ch.prompt,
                'options': opts,
            })
        d['choices'] = choices_out

    return d


# ---------------------------------------------------------------------------
# Project → project.yaml dict
# ---------------------------------------------------------------------------

def _project_to_yaml(project, scenes) -> dict:
    d = {
        'project_id': project.name.lower().replace(' ', '_'),
        'name': project.name,
    }

    start = project.start_scene.scene_id if project.start_scene_id else project.start_scene_str
    if start:
        d['start_scene'] = start

    # Cinema config
    cinema = {}
    for field in ('cinema_image', 'cinema_studio', 'cinema_title', 'cinema_place', 'cinema_text'):
        val = getattr(project, field, '')
        if val:
            cinema[field[len('cinema_'):]] = val
    if cinema:
        d['cinema'] = cinema

    # WA default contact
    wa = project.wa_default_contact.contact_id if project.wa_default_contact_id else project.whatsapp_default_contact
    if wa:
        d['wa_default_contact'] = wa

    # Scene index (order for the compiler)
    d['scenes'] = [s.scene_id for s in scenes]

    # Cases
    cases_out = []
    for case in project.cases.order_by('case_id'):
        c = {
            'case_id': case.case_id,
            'title': case.title,
        }
        if case.description:
            c['description'] = case.description
        start_s = case.start_scene.scene_id if case.start_scene_id else case.start_scene_str
        if start_s:
            c['start_scene'] = start_s
        fields = []
        for cf in case.case_fields.order_by('field_id'):
            fields.append({
                'field_id': cf.field_id,
                'factPath': cf.fact_path,
                'logTemplate': cf.log_template,
                'notificationLabel': cf.notification_label,
            })
        if fields:
            c['fields'] = fields
        cases_out.append(c)
    if cases_out:
        d['cases'] = cases_out

    # Contacts
    contacts = list(project.contacts.order_by('contact_id'))
    if contacts:
        d['contacts'] = [
            {'contact_id': c.contact_id, 'name': c.name, **c.extra_data}
            for c in contacts
        ]

    # Vignettes
    vigs = list(project.vignettes.order_by('vignette_id'))
    if vigs:
        d['vignettes'] = [
            {'vignette_id': v.vignette_id, 'image': v.image,
             **({'label': v.label} if v.label else {}),
             **({'subtitle': v.subtitle} if v.subtitle else {})}
            for v in vigs
        ]

    # Audio assets
    audio = list(project.audio_assets.order_by('asset_type', 'key'))
    if audio:
        d['audio'] = [
            {'type': a.asset_type, 'key': a.key, 'path': a.path}
            for a in audio
        ]

    return d


# ---------------------------------------------------------------------------
# YAML serialization (stdlib-only: no PyYAML dependency)
# ---------------------------------------------------------------------------

def _yaml_dump(obj, indent=0) -> str:
    """
    Minimal YAML serializer. Uses stdlib only — no PyYAML required.
    Produces clean, readable YAML that humans and AI can edit easily.
    """
    pad = '  ' * indent
    child_pad = '  ' * (indent + 1)

    if isinstance(obj, dict):
        if not obj:
            return '{}'
        lines = []
        for k, v in obj.items():
            v_str = _yaml_dump(v, indent + 1)
            if isinstance(v, (dict, list)) and v:
                lines.append(f'{pad}{k}:\n{v_str}')
            else:
                lines.append(f'{pad}{k}: {v_str}')
        return '\n'.join(lines)

    if isinstance(obj, list):
        if not obj:
            return '[]'
        lines = []
        for item in obj:
            item_str = _yaml_dump(item, indent + 1)
            if isinstance(item, dict) and item:
                # First key on same line as dash, rest indented
                first_key = next(iter(item))
                first_val = item[first_key]
                first_val_str = _yaml_dump(first_val, indent + 2)
                rest = {k: v for k, v in item.items() if k != first_key}
                if isinstance(first_val, (dict, list)) and first_val:
                    header = f'{pad}- {first_key}:\n{first_val_str}'
                else:
                    header = f'{pad}- {first_key}: {first_val_str}'
                if rest:
                    rest_str = _yaml_dump(rest, indent + 1)
                    lines.append(f'{header}\n{rest_str}')
                else:
                    lines.append(header)
            else:
                item_str = _yaml_dump(item, indent + 1)
                lines.append(f'{pad}- {item_str.lstrip()}')
        return '\n'.join(lines)

    if isinstance(obj, bool):
        return 'true' if obj else 'false'

    if isinstance(obj, int):
        return str(obj)

    if obj is None:
        return 'null'

    # String — quote if needed
    s = str(obj)
    # Needs quoting if: contains special chars, starts with special YAML chars,
    # is a reserved word, or contains newlines.
    RESERVED = {'true', 'false', 'null', 'yes', 'no', 'on', 'off'}
    needs_quote = (
        not s
        or s.lower() in RESERVED
        or '\n' in s
        or s[0] in '-?:,[]{}#&*!|>\'"@`%'
        or s.endswith(':')
        or ': ' in s
        or s.startswith(' ')
        or s.endswith(' ')
    )
    if needs_quote:
        escaped = s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
        return f'"{escaped}"'
    return s


def _write_yaml(path: Path, data: dict, header: str = '') -> None:
    """Write a dict as YAML to path, with an optional comment header."""
    body = _yaml_dump(data)
    text = (header + '\n' if header else '') + body + '\n'
    path.write_text(text, encoding='utf-8')


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = 'Export Studio DB → YAML files (one per scene + project.yaml)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--project', type=int, default=None,
            help='DB pk of the project to export (default: first project)',
        )
        parser.add_argument(
            '--scene', type=str, default=None,
            help='Export only this scene_id (default: all scenes)',
        )
        parser.add_argument(
            '--out', type=str, default=None,
            help='Output directory (default: <DISPATCHER_DATA_DIR>/../yaml)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Print YAML to stdout instead of writing files',
        )

    def handle(self, *args, **options):
        # Resolve project
        if options['project']:
            project = GameProject.objects.get(pk=options['project'])
        else:
            project = GameProject.objects.prefetch_related(
                'scenes', 'cases', 'cases__case_fields',
                'contacts', 'vignettes', 'audio_assets',
            ).first()
        if not project:
            raise CommandError('No projects found. Import data first.')

        # Resolve output directory
        dry_run = options['dry_run']
        if not dry_run:
            if options['out']:
                out_dir = Path(options['out'])
            else:
                data_dir = Path(getattr(settings, 'DISPATCHER_DATA_DIR',
                                        Path(__file__).resolve().parents[5] / 'dispatcher_cases_v2' / 'data'))
                out_dir = data_dir.parent / 'yaml'
            scenes_dir = out_dir / 'scenes'
            scenes_dir.mkdir(parents=True, exist_ok=True)

        # Load scenes
        scenes_qs = project.scenes.prefetch_related(
            'beats', 'choices', 'choices__options', 'choices__options__beats',
            'next_scene', 'case',
        ).order_by('scene_id')

        if options['scene']:
            scenes_qs = scenes_qs.filter(scene_id=options['scene'])
            if not scenes_qs.exists():
                raise CommandError(f"Scene '{options['scene']}' not found in project '{project.name}'")

        scenes = list(scenes_qs)

        # Export project.yaml (only when exporting all scenes)
        if not options['scene']:
            proj_data = _project_to_yaml(project, scenes)
            header = (
                f'# project.yaml — {project.name}\n'
                f'# Generated by: python manage.py export_yaml\n'
                f'# DO NOT hand-edit game.json — edit here and run export_yaml, '
                f'then compile with export_dispatcher_json.\n'
            )
            if dry_run:
                self.stdout.write(f'\n# ===== project.yaml =====\n')
                self.stdout.write(_yaml_dump(proj_data) + '\n')
            else:
                _write_yaml(out_dir / 'project.yaml', proj_data, header)
                self.stdout.write(f'  Wrote {out_dir / "project.yaml"}')

        # Export each scene
        exported = 0
        for scene in scenes:
            scene_data = _scene_to_yaml(scene)
            header = (
                f'# scene: {scene.scene_id} — {scene.title}\n'
                f'# Edit freely. Import back with: python manage.py import_yaml --scene {scene.scene_id}\n'
            )
            if dry_run:
                self.stdout.write(f'\n# ===== scenes/{scene.scene_id}.yaml =====\n')
                self.stdout.write(_yaml_dump(scene_data) + '\n')
            else:
                _write_yaml(scenes_dir / f'{scene.scene_id}.yaml', scene_data, header)
                beats_n = len(scene_data.get('beats', []))
                choices_n = len(scene_data.get('choices', []))
                self.stdout.write(
                    f'  {scene.scene_id}.yaml  ({beats_n} beats, {choices_n} choices)'
                )
            exported += 1

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(
                f'\nExported {exported} scenes to {out_dir}'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(f'\n[dry-run] {exported} scenes printed.'))
