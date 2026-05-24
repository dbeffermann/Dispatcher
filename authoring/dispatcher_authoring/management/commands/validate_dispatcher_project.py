"""
python manage.py validate_dispatcher_project

Validates referential integrity and structural rules across the project:

  - Scene.next_scene_id → must point to an existing scene (or be blank)
  - ChoiceOption.goto → must point to an existing scene (or be blank)
  - Beat(kind=input).input_field → must reference an existing CaseField
  - Beat(kind=tool-search).tool_ref → must reference an existing ToolSearch
  - Beat(kind=vignette).vignette_ref → must reference an existing Vignette
  - Scenes with channel="911" are listed as emergency scenes
  - Each Case with scenes has a DispatchRule
  - DispatchOutcome match_type is either 'all_required' or blank (with match_units)
"""
from django.core.management.base import BaseCommand, CommandError

from dispatcher_authoring.models import (
    Beat,
    Case,
    GameProject,
    OptionBeat,
    Scene,
)


class Command(BaseCommand):
    help = 'Validate referential integrity of the dispatcher project'

    def add_arguments(self, parser):
        parser.add_argument(
            '--project-id',
            type=int,
            default=None,
            help='GameProject PK to validate (default: first project found)',
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

        errors = []
        warnings = []

        self.stdout.write(f'Validating project: {project.name}')

        # Build lookup sets
        scene_ids = set(project.scenes.values_list('scene_id', flat=True))
        vignette_ids = set(project.vignettes.values_list('vignette_id', flat=True))

        # Per-case lookups
        case_field_ids = {}   # case_id → set of field_ids
        tool_search_ids = {}  # case_id → set of search_ids

        for case in project.cases.prefetch_related('case_fields', 'tool_searches'):
            case_field_ids[case.case_id] = set(case.case_fields.values_list('field_id', flat=True))
            tool_search_ids[case.case_id] = set(case.tool_searches.values_list('search_id', flat=True))

        # Build scene → case mapping by following full scene chains (BFS)
        scene_to_cases = _build_scene_case_map(project)

        # ----------------------------------------------------------------
        # 1. start_scene_id must exist
        # ----------------------------------------------------------------
        project_start = project.effective_start_scene_id
        if project_start and project_start not in scene_ids:
            errors.append(
                f'[GameProject] start_scene="{project_start}" does not exist in scenes'
            )

        # ----------------------------------------------------------------
        # 2. Scene.next_scene_id
        # ----------------------------------------------------------------
        for scene in project.scenes.all():
            next_scene_id = scene.effective_next_scene_id
            if next_scene_id and next_scene_id not in scene_ids:
                errors.append(
                    f'[Scene:{scene.scene_id}] next="{next_scene_id}" '
                    f'does not reference an existing scene'
                )

        # ----------------------------------------------------------------
        # 3. ChoiceOption.goto
        # ----------------------------------------------------------------
        from dispatcher_authoring.models import ChoiceOption
        for opt in ChoiceOption.objects.filter(choice__scene__project=project).select_related('goto_scene'):
            goto = opt.effective_goto
            if goto and goto not in scene_ids:
                errors.append(
                    f'[ChoiceOption:{opt.id} "{opt.label[:40]}"] '
                    f'goto="{goto}" does not reference an existing scene'
                )

        # ----------------------------------------------------------------
        # 4. Beat validations (Scene beats)
        # ----------------------------------------------------------------
        for beat in Beat.objects.filter(scene__project=project).select_related('scene'):
            scene = beat.scene
            case_ids_for_scene = scene_to_cases.get(scene.scene_id, [])

            if beat.kind == Beat.VIGNETTE:
                vignette_id = beat.vignette.vignette_id if beat.vignette_id else beat.vignette_ref
                if vignette_id and vignette_id not in vignette_ids:
                    errors.append(
                        f'[Beat:{beat.id} Scene:{scene.scene_id}] '
                        f'vignette_ref="{vignette_id}" not found in vignettes'
                    )

            elif beat.kind == Beat.INPUT:
                input_field = beat.input_case_field.field_id if beat.input_case_field_id else beat.input_field
                if not input_field:
                    warnings.append(
                        f'[Beat:{beat.id} Scene:{scene.scene_id}] '
                        f'kind=input but input_field is empty'
                    )
                else:
                    _validate_field_ref(
                        input_field, case_ids_for_scene, case_field_ids,
                        f'Beat:{beat.id} Scene:{scene.scene_id}', errors, warnings,
                    )

            elif beat.kind == Beat.TOOL_SEARCH:
                tool_ref = beat.tool_search.search_id if beat.tool_search_id else beat.tool_ref
                if not tool_ref:
                    warnings.append(
                        f'[Beat:{beat.id} Scene:{scene.scene_id}] '
                        f'kind=tool-search but tool_ref is empty'
                    )
                else:
                    _validate_tool_ref(
                        tool_ref, case_ids_for_scene, tool_search_ids,
                        f'Beat:{beat.id} Scene:{scene.scene_id}', errors, warnings,
                    )

        # ----------------------------------------------------------------
        # 5. OptionBeat validations
        # ----------------------------------------------------------------
        for beat in OptionBeat.objects.filter(
            option__choice__scene__project=project
        ).select_related('option__choice__scene'):
            scene = beat.option.choice.scene
            case_ids_for_scene = scene_to_cases.get(scene.scene_id, [])

            if beat.kind == Beat.VIGNETTE:
                vignette_id = beat.vignette.vignette_id if beat.vignette_id else beat.vignette_ref
                if vignette_id and vignette_id not in vignette_ids:
                    errors.append(
                        f'[OptionBeat:{beat.id} Scene:{scene.scene_id}] '
                        f'vignette_ref="{vignette_id}" not found in vignettes'
                    )

            elif beat.kind == Beat.INPUT:
                input_field = beat.input_case_field.field_id if beat.input_case_field_id else beat.input_field
                if input_field:
                    _validate_field_ref(
                        input_field, case_ids_for_scene, case_field_ids,
                        f'OptionBeat:{beat.id} Scene:{scene.scene_id}', errors, warnings,
                    )

            elif beat.kind == Beat.TOOL_SEARCH:
                tool_ref = beat.tool_search.search_id if beat.tool_search_id else beat.tool_ref
                if tool_ref:
                    _validate_tool_ref(
                        tool_ref, case_ids_for_scene, tool_search_ids,
                        f'OptionBeat:{beat.id} Scene:{scene.scene_id}', errors, warnings,
                    )

        # ----------------------------------------------------------------
        # 6. Cases must have DispatchRule
        # ----------------------------------------------------------------
        for case in project.cases.all():
            if not hasattr(case, 'dispatch_rule'):
                warnings.append(
                    f'[Case:{case.case_id}] has no DispatchRule — dispatch will not work'
                )

        # ----------------------------------------------------------------
        # 7. DispatchOutcome integrity
        # ----------------------------------------------------------------
        from dispatcher_authoring.models import DispatchOutcome
        for outcome in DispatchOutcome.objects.filter(rule__case__project=project):
            if not outcome.match_type and not outcome.match_units:
                warnings.append(
                    f'[DispatchOutcome:{outcome.outcome_id}] '
                    f'has neither match_type nor match_units — will never trigger'
                )
            # Check next_scene_id of the parent rule
            rule = outcome.rule
            next_scene_id = rule.effective_next_scene_id
            if next_scene_id and next_scene_id not in scene_ids:
                errors.append(
                    f'[DispatchRule case:{rule.case.case_id}] '
                    f'next_scene="{next_scene_id}" does not exist in scenes'
                )

        # ----------------------------------------------------------------
        # 8. Emergency scenes report
        # ----------------------------------------------------------------
        emergency_scenes = [
            s.scene_id for s in project.scenes.filter(channel='911')
        ]
        if emergency_scenes:
            self.stdout.write(
                f'\nEmergency scenes (channel=911): {", ".join(emergency_scenes)}'
            )
        else:
            warnings.append('No scenes with channel="911" found — no emergency scenes defined')

        # ----------------------------------------------------------------
        # Summary
        # ----------------------------------------------------------------
        self.stdout.write('')
        if errors:
            for e in errors:
                self.stdout.write(self.style.ERROR(f'  ERROR   {e}'))
        if warnings:
            for w in warnings:
                self.stdout.write(self.style.WARNING(f'  WARNING {w}'))

        if not errors and not warnings:
            self.stdout.write(self.style.SUCCESS('All checks passed — no issues found.'))
        elif not errors:
            self.stdout.write(self.style.WARNING(f'{len(warnings)} warning(s), 0 errors.'))
        else:
            self.stdout.write(
                self.style.ERROR(f'{len(errors)} error(s), {len(warnings)} warning(s).')
            )
            raise CommandError('Validation failed with errors.')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_scene_case_map(project) -> dict:
    """
    Returns {scene_id: [case_id, ...]} by BFS-following every scene reachable
    from each case's start_scene (via next_scene_id and choice option goto).
    """
    from dispatcher_authoring.models import ChoiceOption

    # Build adjacency list: scene_id → list of directly reachable scene_ids
    adjacency: dict[str, list[str]] = {}
    for scene in project.scenes.select_related('next_scene'):
        neighbors: list[str] = []
        if scene.effective_next_scene_id:
            neighbors.append(scene.effective_next_scene_id)
        for opt in ChoiceOption.objects.filter(choice__scene=scene).select_related('goto_scene'):
            if opt.effective_goto:
                neighbors.append(opt.effective_goto)
        adjacency[scene.scene_id] = neighbors

    scene_to_cases: dict[str, list[str]] = {}

    for case in project.cases.select_related('start_scene'):
        visited: set[str] = set()
        stack = [case.effective_start_scene_id]
        while stack:
            sid = stack.pop()
            if sid in visited or sid not in adjacency:
                continue
            visited.add(sid)
            stack.extend(adjacency[sid])

        for sid in visited:
            scene_to_cases.setdefault(sid, []).append(case.case_id)

    return scene_to_cases


def _validate_field_ref(field_id, case_ids, case_field_ids, location, errors, warnings):
    """Check that field_id exists in at least one of the associated cases."""
    if not case_ids:
        warnings.append(
            f'[{location}] kind=input field="{field_id}" — '
            f'scene not linked to any case; cannot verify CaseField reference'
        )
        return
    found = any(field_id in case_field_ids.get(cid, set()) for cid in case_ids)
    if not found:
        errors.append(
            f'[{location}] kind=input field="{field_id}" '
            f'not found in case_fields for cases: {case_ids}'
        )


def _validate_tool_ref(tool_id, case_ids, tool_search_ids, location, errors, warnings):
    """Check that tool_id exists in at least one of the associated cases."""
    if not case_ids:
        warnings.append(
            f'[{location}] kind=tool-search tool="{tool_id}" — '
            f'scene not linked to any case; cannot verify ToolSearch reference'
        )
        return
    found = any(tool_id in tool_search_ids.get(cid, set()) for cid in case_ids)
    if not found:
        errors.append(
            f'[{location}] kind=tool-search tool="{tool_id}" '
            f'not found in tool_searches for cases: {case_ids}'
        )
