"""
python manage.py export_dispatcher_json

Exports the Django database back to dispatcher_story.json and dispatcher_assets.json,
preserving the exact structure that dispatcher_console.html expects.

By default writes to settings.DISPATCHER_DATA_DIR (the live game data folder).
Use --dry-run to print to stdout instead of writing files.
"""
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from dispatcher_authoring.models import GameProject


def _normalize_asset_path(path: str) -> str:
    """Normalize legacy asset paths to runtime-served relative paths."""
    p = str(path or '').strip().replace('\\', '/')
    if not p:
        return ''
    if p.startswith('/_assets/'):
        return 'assets/' + p[len('/_assets/'):]
    if p.startswith('_assets/'):
        return 'assets/' + p[len('_assets/'):]
    if p.startswith('/assets/'):
        return p[1:]
    return p


# ---------------------------------------------------------------------------
# Beat serialization
# ---------------------------------------------------------------------------

def _beat_to_json(beat) -> dict:
    """Convert a Beat or OptionBeat model instance to the JSON dict expected by the game."""
    return beat.to_json()


def _beats_list_to_json(beats_qs) -> list:
    return [_beat_to_json(b) for b in beats_qs.order_by('order')]


# ---------------------------------------------------------------------------
# Choice serialization
# ---------------------------------------------------------------------------

def _choices_to_json(scene) -> list:
    result = []
    for choice in scene.choices.order_by('at_beat'):
        options = []
        for opt in choice.options.order_by('order'):
            opt_dict = {'label': opt.label}
            goto = opt.goto_scene.scene_id if opt.goto_scene_id else opt.goto
            if goto:
                opt_dict['goto'] = goto
            opt_dict['beats'] = _beats_list_to_json(opt.beats)
            options.append(opt_dict)
        result.append({
            'at': choice.at_beat,
            'prompt': choice.prompt,
            'options': options,
        })
    return result


# ---------------------------------------------------------------------------
# Scene serialization
# ---------------------------------------------------------------------------

def _scene_to_json(scene) -> dict:
    d = {'id': scene.scene_id}
    if scene.title:
        d['title'] = scene.title
    if scene.objective:
        d['objective'] = scene.objective
    d['beats'] = _beats_list_to_json(scene.beats)
    choices = _choices_to_json(scene)
    if choices:
        d['choices'] = choices
    next_id = scene.next_scene.scene_id if scene.next_scene_id else scene.next_scene_str
    if next_id:
        d['next'] = next_id
    if scene.channel:
        d['channel'] = scene.channel
    if scene.case_id:
        d['case_id'] = scene.case.case_id
    return d


# ---------------------------------------------------------------------------
# Case serialization
# ---------------------------------------------------------------------------

def _case_fields_to_json(case) -> dict:
    result = {}
    for cf in case.case_fields.order_by('field_id'):
        result[cf.field_id] = {
            'factPath': cf.fact_path,
            'logTemplate': cf.log_template,
            'notificationLabel': cf.notification_label,
        }
    return result


def _tool_search_to_json(ts) -> dict:
    d = {'title': ts.title}
    if ts.hint:
        d['hint'] = ts.hint
    if ts.placeholder:
        d['placeholder'] = ts.placeholder
    if ts.match_patterns:
        d['match_patterns'] = ts.match_patterns
    # Merge extra data (contains 'result', 'models', or other keys)
    if ts.result_data:
        d.update(ts.result_data)
    return d


def _dispatch_outcome_to_json(outcome) -> dict:
    d = {'id': outcome.outcome_id}
    if outcome.match_type:
        d['match'] = outcome.match_type
    if outcome.match_units:
        d['match_units'] = outcome.match_units
    d['notification'] = outcome.notification
    d['beats'] = outcome.beats_json
    return d


def _dispatch_rules_to_json(rule) -> dict:
    next_id = rule.next_scene.scene_id if rule.next_scene_id else rule.next_scene_str
    return {
        'case_title': rule.case_title,
        'available_units': rule.available_units,
        'required_units': rule.required_units,
        'next': next_id,
        'outcomes': [
            _dispatch_outcome_to_json(o)
            for o in rule.outcomes.order_by('outcome_id')
        ],
    }


def _infer_project_start_scene_id(project) -> str:
    scene_ids = set(project.scenes.values_list('scene_id', flat=True))
    if not scene_ids:
        return ''

    incoming = {sid: 0 for sid in scene_ids}
    outgoing = {sid: 0 for sid in scene_ids}

    def add_link(from_id, to_id):
        if from_id in scene_ids and to_id in scene_ids:
            outgoing[from_id] += 1
            incoming[to_id] += 1

    scenes = list(
        project.scenes
        .select_related('next_scene', 'case')
        .prefetch_related('choices__options__goto_scene', 'beats')
    )
    for scene in scenes:
        add_link(scene.scene_id, scene.effective_next_scene_id)
        for choice in scene.choices.all():
            for option in choice.options.all():
                add_link(scene.scene_id, option.effective_goto)

    dispatch_next_by_case = {}
    for case in project.cases.select_related('dispatch_rule__next_scene'):
        if hasattr(case, 'dispatch_rule'):
            dispatch_next_by_case[case.case_id] = case.dispatch_rule.effective_next_scene_id
    for scene in scenes:
        dispatch_next = dispatch_next_by_case.get(scene.case.case_id if scene.case_id else '')
        if dispatch_next and any(beat.kind == 'dispatch' for beat in scene.beats.all()):
            add_link(scene.scene_id, dispatch_next)

    source_candidates = sorted(
        sid for sid in scene_ids
        if outgoing[sid] and incoming[sid] == 0
    )
    configured = project.start_scene.scene_id if project.start_scene_id else project.start_scene_str
    if len(source_candidates) == 1:
        return source_candidates[0]
    if configured in scene_ids:
        return configured
    if source_candidates:
        return source_candidates[0]

    case_starts = [
        case.start_scene.scene_id if case.start_scene_id else case.start_scene_str
        for case in project.cases.select_related('start_scene')
    ]
    case_starts = sorted({sid for sid in case_starts if sid in scene_ids})
    if case_starts:
        return case_starts[0]
    return sorted(scene_ids)[0]


def _case_to_json(case) -> dict:
    start_id = case.start_scene.scene_id if case.start_scene_id else case.start_scene_str
    d = {
        'id': case.case_id,
        'title': case.title,
        'description': case.description,
        'start_scene': start_id,
        'case_fields': _case_fields_to_json(case),
        'tool_search': {
            ts.search_id: _tool_search_to_json(ts)
            for ts in case.tool_searches.order_by('search_id')
        },
    }
    if hasattr(case, 'dispatch_rule'):
        d['dispatch_rules'] = _dispatch_rules_to_json(case.dispatch_rule)
    return d


# ---------------------------------------------------------------------------
# Full story JSON
# ---------------------------------------------------------------------------

def build_story_json(project) -> dict:
    start_id = _infer_project_start_scene_id(project)
    story = {
        'start': start_id,
        'manual_categories': [
            {
                'id': cat.cat_id,
                'name': cat.name,
                'keys': cat.keys,
                'when': cat.when,
                'steps': cat.steps,
                'avoid': cat.avoid,
                'dispatch': cat.dispatch,
            }
            for cat in project.manual_categories.order_by('order')
        ],
        'scenes': [
            _scene_to_json(scene)
            for scene in project.scenes.select_related(
                'next_scene', 'case'
            ).prefetch_related(
                'beats__vignette', 'beats__input_case_field', 'beats__tool_search',
                'choices__options__beats__vignette',
                'choices__options__beats__input_case_field',
                'choices__options__beats__tool_search',
                'choices__options__goto_scene',
            ).order_by('scene_id')
        ],
        'cases': [
            _case_to_json(case)
            for case in project.cases.select_related(
                'start_scene', 'dispatch_rule__next_scene'
            ).prefetch_related(
                'case_fields', 'tool_searches',
                'dispatch_rule__outcomes',
            ).order_by('case_id')
        ],
    }
    return story


# ---------------------------------------------------------------------------
# Full assets JSON
# ---------------------------------------------------------------------------

def build_assets_json(project) -> dict:
    music = {
        a.key: _normalize_asset_path(a.path)
        for a in project.audio_assets.filter(asset_type='music').order_by('key')
    }
    sfx = {
        a.key: _normalize_asset_path(a.path)
        for a in project.audio_assets.filter(asset_type='sfx').order_by('key')
    }
    vignettes = {
        v.vignette_id: {
            'image': v.image,
            'label': v.label,
            'subtitle': v.subtitle,
        }
        for v in project.vignettes.order_by('vignette_id')
    }
    contacts = {}
    for c in project.contacts.order_by('contact_id'):
        contacts[c.contact_id] = {'name': c.name, **c.extra_data}

    wa_contact_id = project.wa_default_contact.contact_id if project.wa_default_contact_id else project.whatsapp_default_contact
    assets = {
        'audio': {
            'music': music,
            'sfx': sfx,
        },
        'vignettes': vignettes,
        'cinema': {
            'image': project.cinema_image,
            'studio': project.cinema_studio,
            'title': project.cinema_title,
            'place': project.cinema_place,
            'text': project.cinema_text,
        },
        'whatsapp': {
            'default_contact': wa_contact_id,
        },
        'contacts': contacts,
    }
    return assets


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = 'Export Django DB back to dispatcher_story.json and dispatcher_assets.json'

    def add_arguments(self, parser):
        parser.add_argument(
            '--data-dir',
            type=str,
            default=None,
            help='Override output directory (default: settings.DISPATCHER_DATA_DIR)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print JSON to stdout instead of writing files',
        )
        parser.add_argument(
            '--project-id',
            type=int,
            default=None,
            help='GameProject PK to export (default: first project found)',
        )

    def handle(self, *args, **options):
        if options['project_id']:
            try:
                project = GameProject.objects.get(pk=options['project_id'])
            except GameProject.DoesNotExist:
                raise CommandError(f'GameProject with pk={options["project_id"]} not found')
        else:
            project = GameProject.objects.first()
            if project is None:
                raise CommandError('No GameProject found. Run import_dispatcher_json first.')

        self.stdout.write(f'Exporting project: {project.name}')

        story_json = build_story_json(project)
        assets_json = build_assets_json(project)

        story_str = json.dumps(story_json, ensure_ascii=False, indent=2)
        assets_str = json.dumps(assets_json, ensure_ascii=False, indent=2)

        if options['dry_run']:
            self.stdout.write('=== dispatcher_story.json ===')
            self.stdout.write(story_str)
            self.stdout.write('=== dispatcher_assets.json ===')
            self.stdout.write(assets_str)
            return

        data_dir = Path(options['data_dir']) if options['data_dir'] else Path(settings.DISPATCHER_DATA_DIR)
        data_dir.mkdir(parents=True, exist_ok=True)

        story_path = data_dir / 'dispatcher_story.json'
        assets_path = data_dir / 'dispatcher_assets.json'

        story_path.write_text(story_str, encoding='utf-8')
        assets_path.write_text(assets_str, encoding='utf-8')

        self.stdout.write(self.style.SUCCESS(f'Wrote {story_path}'))
        self.stdout.write(self.style.SUCCESS(f'Wrote {assets_path}'))

        scene_count = project.scenes.count()
        case_count = project.cases.count()
        self.stdout.write(
            f'  Exported {scene_count} scenes, {case_count} cases'
        )
