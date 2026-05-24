"""
python manage.py import_dispatcher_json

Imports dispatcher_story.json and dispatcher_assets.json into the Django database.
Safe to run multiple times — existing data is cleared and reimported (idempotent).

Data files are resolved relative to settings.DISPATCHER_DATA_DIR.
"""
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from dispatcher_authoring.models import (
    AudioAsset,
    Beat,
    Case,
    CaseField,
    Choice,
    ChoiceOption,
    Contact,
    DispatchOutcome,
    DispatchRule,
    GameProject,
    ManualCategory,
    OptionBeat,
    Scene,
    ToolSearch,
    Vignette,
)


def _load_json(path: Path, label: str) -> dict:
    if not path.exists():
        raise CommandError(f'{label} not found at: {path}')
    with path.open(encoding='utf-8') as f:
        return json.load(f)


def _import_beat(beat_data: dict, order: int, *, scene=None, option=None) -> None:
    """Create a Beat (for a Scene) or OptionBeat (for a ChoiceOption)."""
    kind = beat_data.get('kind', '')  # absent = dialogue beat

    common = dict(
        order=order,
        kind=kind,
        text=beat_data.get('text', ''),
        speaker=beat_data.get('speaker', ''),
        vignette_ref=beat_data.get('scene', '') if kind == 'vignette' else '',
        input_field=beat_data.get('field', '') if kind == 'input' else '',
        input_set_value=beat_data.get('setValue', '') if kind == 'input' else '',
        input_answer=beat_data.get('answer', '') if kind == 'input' else '',
        input_hint=beat_data.get('hint', '') if kind == 'input' else '',
        tool_ref=beat_data.get('tool', '') if kind == 'tool-search' else '',
        wa_contact=beat_data.get('contact', '') if kind == 'wa' else '',
        media_type=beat_data.get('mediaType', '') if kind == 'wa' else '',
        media_url=beat_data.get('url', '') if kind == 'wa' else '',
        media_caption=beat_data.get('caption', '') if kind == 'wa' else '',
        media_name=beat_data.get('name', '') if kind == 'wa' else '',
        media_duration=beat_data.get('dur', '') if kind == 'wa' else '',
        sfx_key=beat_data.get('sfx', '') if kind == 'sfx' else '',
        ambient_key=beat_data.get('ambient', '') if kind == 'ambient' else '',
        ambient_stop=bool(beat_data.get('stop')) if kind == 'ambient' else False,
    )

    if scene is not None:
        Beat.objects.create(scene=scene, **common)
    elif option is not None:
        OptionBeat.objects.create(option=option, **common)


class Command(BaseCommand):
    help = 'Import dispatcher_story.json and dispatcher_assets.json into Django DB'

    def add_arguments(self, parser):
        parser.add_argument(
            '--data-dir',
            type=str,
            default=None,
            help='Override path to the data directory (default: settings.DISPATCHER_DATA_DIR)',
        )
        parser.add_argument(
            '--project-name',
            type=str,
            default='Dispatcher',
            help='Name for the GameProject record (default: "Dispatcher")',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        data_dir = Path(options['data_dir']) if options['data_dir'] else Path(settings.DISPATCHER_DATA_DIR)

        story = _load_json(data_dir / 'dispatcher_story.json', 'dispatcher_story.json')
        assets = _load_json(data_dir / 'dispatcher_assets.json', 'dispatcher_assets.json')

        self.stdout.write('Clearing existing data...')
        GameProject.objects.all().delete()  # cascade deletes everything

        # ----------------------------------------------------------------
        # GameProject
        # ----------------------------------------------------------------
        cinema = assets.get('cinema', {})
        whatsapp = assets.get('whatsapp', {})

        project = GameProject.objects.create(
            name=options['project_name'],
            start_scene_str=story.get('start', 'intro'),
            cinema_image=cinema.get('image', ''),
            cinema_studio=cinema.get('studio', ''),
            cinema_title=cinema.get('title', ''),
            cinema_place=cinema.get('place', ''),
            cinema_text=cinema.get('text', ''),
            whatsapp_default_contact=whatsapp.get('default_contact', ''),
        )
        self.stdout.write(f'  Created project: {project.name}')

        # ----------------------------------------------------------------
        # ManualCategories
        # ----------------------------------------------------------------
        for order, cat in enumerate(story.get('manual_categories', [])):
            ManualCategory.objects.create(
                project=project,
                order=order,
                cat_id=cat.get('id', ''),
                name=cat.get('name', ''),
                keys=cat.get('keys', []),
                when=cat.get('when', ''),
                steps=cat.get('steps', []),
                avoid=cat.get('avoid', []),
                dispatch=cat.get('dispatch', ''),
            )
        count = project.manual_categories.count()
        self.stdout.write(f'  Imported {count} manual categories')

        # ----------------------------------------------------------------
        # Scenes (beats + choices)
        # ----------------------------------------------------------------
        scenes_imported = 0
        for scene_data in story.get('scenes', []):
            scene = Scene.objects.create(
                project=project,
                scene_id=scene_data['id'],
                title=scene_data.get('title') or '',
                objective=scene_data.get('objective') or '',
                channel=scene_data.get('channel') or '',
                next_scene_str=scene_data.get('next') or '',
            )
            scenes_imported += 1

            # Beats
            for i, beat_data in enumerate(scene_data.get('beats', [])):
                _import_beat(beat_data, order=i, scene=scene)

            # Choices
            for choice_data in scene_data.get('choices', []):
                choice = Choice.objects.create(
                    scene=scene,
                    at_beat=choice_data.get('at', 0),
                    prompt=choice_data.get('prompt', ''),
                )
                for opt_order, opt_data in enumerate(choice_data.get('options', [])):
                    option = ChoiceOption.objects.create(
                        choice=choice,
                        order=opt_order,
                        label=opt_data.get('label', ''),
                        goto=opt_data.get('goto', ''),
                    )

        self.stdout.write(f'  Imported {scenes_imported} scenes')

        # ----------------------------------------------------------------
        # Cases
        # ----------------------------------------------------------------
        cases_imported = 0
        for case_data in story.get('cases', []):
            case = Case.objects.create(
                project=project,
                case_id=case_data['id'],
                title=case_data.get('title', ''),
                description=case_data.get('description', ''),
                start_scene_str=case_data.get('start_scene', ''),
            )
            cases_imported += 1

            # CaseFields
            for field_id, field_data in case_data.get('case_fields', {}).items():
                CaseField.objects.create(
                    case=case,
                    field_id=field_id,
                    fact_path=field_data.get('factPath', ''),
                    log_template=field_data.get('logTemplate', ''),
                    notification_label=field_data.get('notificationLabel', ''),
                )

            # ToolSearches
            for search_id, ts_data in case_data.get('tool_search', {}).items():
                known_keys = {'title', 'hint', 'placeholder', 'match_patterns'}
                result_data = {k: v for k, v in ts_data.items() if k not in known_keys}
                ToolSearch.objects.create(
                    case=case,
                    search_id=search_id,
                    title=ts_data.get('title', ''),
                    hint=ts_data.get('hint', ''),
                    placeholder=ts_data.get('placeholder', ''),
                    match_patterns=ts_data.get('match_patterns', []),
                    result_data=result_data,
                )

            # DispatchRule + Outcomes
            dr_data = case_data.get('dispatch_rules', {})
            if dr_data:
                rule = DispatchRule.objects.create(
                    case=case,
                    case_title=dr_data.get('case_title', ''),
                    available_units=dr_data.get('available_units', []),
                    required_units=dr_data.get('required_units', []),
                    next_scene_str=dr_data.get('next', ''),
                )
                for outcome_data in dr_data.get('outcomes', []):
                    DispatchOutcome.objects.create(
                        rule=rule,
                        outcome_id=outcome_data.get('id', ''),
                        match_type=outcome_data.get('match', ''),
                        match_units=outcome_data.get('match_units', []),
                        notification=outcome_data.get('notification', ''),
                        beats_json=outcome_data.get('beats', []),
                    )

        self.stdout.write(f'  Imported {cases_imported} cases')

        # ----------------------------------------------------------------
        # Assets — audio
        # ----------------------------------------------------------------
        audio = assets.get('audio', {})
        for key, path in audio.get('music', {}).items():
            AudioAsset.objects.create(project=project, asset_type='music', key=key, path=path)
        for key, path in audio.get('sfx', {}).items():
            AudioAsset.objects.create(project=project, asset_type='sfx', key=key, path=path)
        self.stdout.write(f'  Imported {project.audio_assets.count()} audio assets')

        # ----------------------------------------------------------------
        # Assets — vignettes
        # ----------------------------------------------------------------
        for vig_id, vig_data in assets.get('vignettes', {}).items():
            Vignette.objects.create(
                project=project,
                vignette_id=vig_id,
                image=vig_data.get('image', ''),
                label=vig_data.get('label', ''),
                subtitle=vig_data.get('subtitle', ''),
            )
        self.stdout.write(f'  Imported {project.vignettes.count()} vignettes')

        # ----------------------------------------------------------------
        # Assets — contacts
        # ----------------------------------------------------------------
        for contact_id, contact_data in assets.get('contacts', {}).items():
            name = contact_data.get('name', contact_id)
            extra = {k: v for k, v in contact_data.items() if k != 'name'}
            Contact.objects.create(
                project=project,
                contact_id=contact_id,
                name=name,
                extra_data=extra,
            )
        self.stdout.write(f'  Imported {project.contacts.count()} contacts')

        # ----------------------------------------------------------------
        # Second pass: populate FK fields from str IDs
        # ----------------------------------------------------------------
        self._populate_fk_fields(project)
        self.stdout.write('  Populated FK references')

        self.stdout.write(self.style.SUCCESS('Import completed successfully.'))

    @staticmethod
    def _populate_fk_fields(project):
        """After all objects are created, resolve str-based IDs into FK references."""
        from django.apps import apps

        # Build lookup maps scoped to this project
        scene_map = {s.scene_id: s for s in project.scenes.all()}
        vignette_map = {v.vignette_id: v for v in project.vignettes.all()}
        contact_map = {c.contact_id: c for c in project.contacts.all()}
        field_map = {f.field_id: f for case in project.cases.prefetch_related('case_fields').all()
                     for f in case.case_fields.all()}
        tool_map = {t.search_id: t for case in project.cases.prefetch_related('tool_searches').all()
                    for t in case.tool_searches.all()}

        # GameProject
        if project.start_scene_str in scene_map:
            project.start_scene = scene_map[project.start_scene_str]
        if project.whatsapp_default_contact in contact_map:
            project.wa_default_contact = contact_map[project.whatsapp_default_contact]
        project.save(update_fields=['start_scene', 'wa_default_contact'])

        # Scenes
        scene_updates = []
        for scene in project.scenes.all():
            changed = False
            if scene.next_scene_str in scene_map:
                scene.next_scene = scene_map[scene.next_scene_str]
                changed = True
            if changed:
                scene_updates.append(scene)
        if scene_updates:
            project.scenes.model.objects.bulk_update(scene_updates, ['next_scene'])

        # ChoiceOptions
        option_updates = []
        for case in project.cases.all():
            for scene in project.scenes.all():
                for choice in scene.choices.prefetch_related('options').all():
                    for opt in choice.options.all():
                        if opt.goto in scene_map:
                            opt.goto_scene = scene_map[opt.goto]
                            option_updates.append(opt)
        if option_updates:
            ChoiceOption = apps.get_model('dispatcher_authoring', 'ChoiceOption')
            ChoiceOption.objects.bulk_update(option_updates, ['goto_scene'])

        # Cases
        case_updates = []
        for case in project.cases.all():
            if case.start_scene_str in scene_map:
                case.start_scene = scene_map[case.start_scene_str]
                case_updates.append(case)
        if case_updates:
            project.cases.model.objects.bulk_update(case_updates, ['start_scene'])

        # DispatchRules
        rule_updates = []
        for case in project.cases.prefetch_related('dispatch_rule').all():
            if hasattr(case, 'dispatch_rule'):
                rule = case.dispatch_rule
                if rule.next_scene_str in scene_map:
                    rule.next_scene = scene_map[rule.next_scene_str]
                    rule_updates.append(rule)
        if rule_updates:
            DispatchRule = apps.get_model('dispatcher_authoring', 'DispatchRule')
            DispatchRule.objects.bulk_update(rule_updates, ['next_scene'])

        # Beats and OptionBeats
        Beat = apps.get_model('dispatcher_authoring', 'Beat')
        OptionBeat = apps.get_model('dispatcher_authoring', 'OptionBeat')
        for model_cls in (Beat, OptionBeat):
            beat_updates = []
            for beat in model_cls.objects.filter(
                Q(scene__project=project) if model_cls == Beat
                else Q(option__choice__scene__project=project)
            ).select_related('vignette', 'input_case_field', 'tool_search'):
                changed = False
                if not beat.vignette_id and beat.vignette_ref in vignette_map:
                    beat.vignette = vignette_map[beat.vignette_ref]
                    changed = True
                if not beat.input_case_field_id and beat.input_field in field_map:
                    beat.input_case_field = field_map[beat.input_field]
                    changed = True
                if not beat.tool_search_id and beat.tool_ref in tool_map:
                    beat.tool_search = tool_map[beat.tool_ref]
                    changed = True
                if changed:
                    beat_updates.append(beat)
            if beat_updates:
                model_cls.objects.bulk_update(beat_updates, ['vignette', 'input_case_field', 'tool_search'])

