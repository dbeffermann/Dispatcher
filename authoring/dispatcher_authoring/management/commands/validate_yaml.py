"""
python manage.py validate_yaml [--profile <path>] [--scene <scene_id>]
                               [--dir <dir>] [--strict] [--no-graph]

Validates YAML scene files against a game profile.

Checks per scene:
  - channel is in profile.channels
  - beat.kind is in profile.beat_kinds (in scene beats AND option beats)
  - beat required fields are present
  - choice_option.goto references an existing scene_id
  - scene.next references an existing scene_id

Cross-scene checks (from profile validations list):
  - goto_exists: all goto values exist
  - next_exists: all next values exist
  - input_requires_case: scenes with input beats need case
  - dispatch_requires_case: scenes with dispatch beats need case
  - choice_has_options: all choices have at least one option
  - no_orphan_scenes: all scenes reachable from start_scene

Scene graph output:
  - Tree view from start_scene following next + all goto links
  - Unreachable scenes listed
  - Dead-end scenes (no next, no goto) listed
  - Loop detection

Exit codes: 0 = pass, 1 = errors found (or warnings with --strict)
"""
import sys
from collections import defaultdict, deque
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from dispatcher_authoring.yaml_io import load_yaml_file


# ---------------------------------------------------------------------------
# Scene-level validation
# ---------------------------------------------------------------------------

def _validate_beats(beats: list, valid_kinds: set, required_by_kind: dict,
                    scene_id: str, location: str) -> tuple[list, list]:
    """Validate a list of beats. Returns (errors, warnings)."""
    errors, warnings = [], []
    for i, beat in enumerate(beats or []):
        if not isinstance(beat, dict):
            continue
        kind = beat.get('kind', '')
        loc = f'{location} beat[{i}]'
        if kind not in valid_kinds:
            errors.append(f'{loc}: unknown kind {kind!r}')
        else:
            for req in (required_by_kind.get(kind) or []):
                if req not in beat:
                    errors.append(f'{loc} (kind={kind!r}): missing required field {req!r}')
    return errors, warnings


def _validate_scene(data: dict, valid_channels: set, valid_kinds: set,
                    required_by_kind: dict, all_scene_ids: set,
                    rules: list) -> tuple[list, list]:
    """
    Validate one scene dict.
    Returns (errors, warnings).
    """
    errors, warnings = [], []
    scene_id = data.get('scene_id', '?')

    # Channel
    channel = str(data.get('channel', '') or '')
    if channel not in valid_channels:
        errors.append(f'invalid channel {channel!r} — not in profile channels')

    # Beats
    be, bw = _validate_beats(
        data.get('beats') or [], valid_kinds, required_by_kind, scene_id, scene_id
    )
    errors.extend(be)
    warnings.extend(bw)

    # Choices
    for ci, choice in enumerate(data.get('choices') or []):
        if not isinstance(choice, dict):
            continue

        opts = choice.get('options') or []

        # Rule: choice_has_options
        if _rule_active('choice_has_options', rules) and not opts:
            level = _rule_level('choice_has_options', rules)
            msg = f'choice[{ci}] (at={choice.get("at")}): no options defined'
            (errors if level == 'error' else warnings).append(msg)

        for oi, opt in enumerate(opts):
            if not isinstance(opt, dict):
                continue
            goto = opt.get('goto', '')
            if goto and goto not in all_scene_ids:
                errors.append(f'choice[{ci}].option[{oi}]: goto {goto!r} does not exist')

            # Option beats
            obe, obw = _validate_beats(
                opt.get('beats') or [], valid_kinds, required_by_kind,
                scene_id, f'{scene_id}.choice[{ci}].opt[{oi}]'
            )
            errors.extend(obe)
            warnings.extend(obw)

    # next
    next_id = data.get('next', '')
    if next_id and next_id not in all_scene_ids:
        errors.append(f'next {next_id!r} does not exist')

    # Case-related rules (use context_ref_alias 'case' for Dispatcher)
    has_case = bool(data.get('case') or data.get('context_ref'))
    all_beats = list(data.get('beats') or [])
    for ch in (data.get('choices') or []):
        for opt in (ch.get('options') or []):
            if isinstance(opt, dict):
                all_beats.extend(opt.get('beats') or [])

    if _rule_active('input_requires_case', rules):
        has_input = any(b.get('kind') == 'input' for b in all_beats if isinstance(b, dict))
        if has_input and not has_case:
            level = _rule_level('input_requires_case', rules)
            msg = 'has input beat(s) but no case association'
            (errors if level == 'error' else warnings).append(msg)

    if _rule_active('dispatch_requires_case', rules):
        has_dispatch = any(b.get('kind') == 'dispatch' for b in all_beats if isinstance(b, dict))
        if has_dispatch and not has_case:
            level = _rule_level('dispatch_requires_case', rules)
            msg = 'has dispatch beat but no case association'
            (errors if level == 'error' else warnings).append(msg)

    return errors, warnings


def _rule_active(rule_id: str, rules: list) -> bool:
    return any(r.get('rule') == rule_id for r in rules)


def _rule_level(rule_id: str, rules: list) -> str:
    for r in rules:
        if r.get('rule') == rule_id:
            return r.get('level', 'warning')
    return 'warning'


# ---------------------------------------------------------------------------
# Scene graph analysis
# ---------------------------------------------------------------------------

def _build_graph(scenes: dict) -> dict:
    """Build adjacency dict: scene_id -> set of scene_ids reachable in one step."""
    adj = defaultdict(set)
    for sid, data in scenes.items():
        nxt = data.get('next', '')
        if nxt:
            adj[sid].add(nxt)
        for ch in (data.get('choices') or []):
            for opt in (ch.get('options') or []):
                goto = opt.get('goto', '')
                if goto:
                    adj[sid].add(goto)
    return adj


def _reachable(adj: dict, start: str, all_ids: set) -> set:
    """BFS: return set of scene_ids reachable from start."""
    visited = set()
    if start not in all_ids:
        return visited
    queue = deque([start])
    visited.add(start)
    while queue:
        node = queue.popleft()
        for nb in adj.get(node, set()):
            if nb in all_ids and nb not in visited:
                visited.add(nb)
                queue.append(nb)
    return visited


def _detect_loops(adj: dict, all_ids: set) -> list:
    """Return list of (scene_id, path) tuples where a loop was detected."""
    loops = []
    visited_global = set()

    def dfs(node, path, on_stack):
        if node not in all_ids:
            return
        if node in on_stack:
            cycle_start = path.index(node)
            loops.append(path[cycle_start:] + [node])
            return
        if node in visited_global:
            return
        visited_global.add(node)
        on_stack.add(node)
        path.append(node)
        for nb in adj.get(node, set()):
            dfs(nb, path, on_stack)
        path.pop()
        on_stack.discard(node)

    for sid in sorted(all_ids):
        if sid not in visited_global:
            dfs(sid, [], set())
    return loops


def _print_tree(sid: str, adj: dict, all_ids: set, scenes: dict,
                prefix: str, visited: set, out: list, depth: int = 0):
    """Recursively print a tree rooted at sid. Appends lines to `out`."""
    label = (scenes.get(sid, {}).get('title') or sid)[:35]
    loop = ' [loop]' if sid in visited else ''
    out.append(f'  {prefix}{sid}  {label}{loop}')
    if sid in visited or depth > 20:
        return
    visited.add(sid)
    children = sorted(adj.get(sid, set()) & all_ids)
    for i, child in enumerate(children):
        is_last = (i == len(children) - 1)
        connector = '`-- ' if is_last else '+-- '
        extension = '    ' if is_last else '|   '
        _print_tree(child, adj, all_ids, scenes, prefix + connector,
                    visited, out, depth + 1)


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = 'Validate YAML scene files against a game profile'

    def add_arguments(self, parser):
        parser.add_argument(
            '--profile', type=str, default=None,
            help='Path to .profile.yaml (default: auto-detect from schemas/profiles/)',
        )
        parser.add_argument(
            '--scene', type=str, default=None,
            help='Validate only this scene_id',
        )
        parser.add_argument(
            '--dir', type=str, default=None,
            help='Root dir containing yaml/ and schemas/ (default: auto-detect)',
        )
        parser.add_argument(
            '--strict', action='store_true',
            help='Treat warnings as errors (exit 1 if any warnings)',
        )
        parser.add_argument(
            '--no-graph', action='store_true',
            help='Skip scene graph output',
        )

    def handle(self, *args, **options):
        # Resolve paths
        data_dir = Path(getattr(settings, 'DISPATCHER_DATA_DIR',
                                Path(__file__).resolve().parents[5]
                                / 'dispatcher_cases_v2' / 'data'))
        project_root = data_dir.parent

        if options['dir']:
            project_root = Path(options['dir'])

        yaml_dir = project_root / 'yaml'
        schemas_dir = project_root / 'schemas'
        scenes_dir = yaml_dir / 'scenes'

        # Load profile
        if options['profile']:
            profile_path = Path(options['profile'])
        else:
            profiles_dir = schemas_dir / 'profiles'
            profiles = sorted(profiles_dir.glob('*.profile.yaml')) if profiles_dir.exists() else []
            if not profiles:
                raise CommandError(
                    f'No profile found in {profiles_dir}. Use --profile <path>.'
                )
            profile_path = profiles[0]

        try:
            profile = load_yaml_file(profile_path)
        except ValueError as e:
            raise CommandError(str(e))

        profile_id = profile.get('profile_id', profile_path.stem)
        profile_ver = profile.get('version', '?')
        self.stdout.write(f'Profile : {profile_id} v{profile_ver}')

        # Build lookup structures
        valid_channels = {
            str(ch.get('id') or '') for ch in (profile.get('channels') or [])
        }
        valid_kinds = {
            (bk.get('kind') or '') for bk in (profile.get('beat_kinds') or [])
        }
        required_by_kind = {
            (bk.get('kind') or ''): (bk.get('required_fields') or [])
            for bk in (profile.get('beat_kinds') or [])
        }
        rules = profile.get('validations') or []

        # Load scenes
        if not scenes_dir.exists():
            raise CommandError(f'Scenes directory not found: {scenes_dir}')

        yaml_files = (
            [scenes_dir / f"{options['scene']}.yaml"]
            if options['scene']
            else sorted(scenes_dir.glob('*.yaml'))
        )
        for f in yaml_files:
            if not f.exists():
                raise CommandError(f'Scene file not found: {f}')

        scenes: dict = {}
        for path in yaml_files:
            try:
                data = load_yaml_file(path)
            except ValueError as e:
                raise CommandError(str(e))
            sid = data.get('scene_id') or path.stem
            scenes[sid] = data

        all_scene_ids = set(scenes.keys())

        # Load project.yaml for start_scene
        project_yaml = yaml_dir / 'project.yaml'
        start_scene = None
        if project_yaml.exists():
            try:
                proj = load_yaml_file(project_yaml)
                # Look for start_scene at project root first, then cases[0].start_scene
                start_scene = proj.get('start_scene')
                if not start_scene:
                    cases = proj.get('cases') or []
                    if cases and isinstance(cases[0], dict):
                        start_scene = cases[0].get('start_scene')
            except ValueError:
                pass

        self.stdout.write(f'Scenes  : {len(scenes)}')
        self.stdout.write(f'Start   : {start_scene or "(unknown — no project.yaml)"}')
        self.stdout.write('')

        # -------------------------------------------------------------------
        # Per-scene validation
        # -------------------------------------------------------------------
        all_errors: list = []
        all_warnings: list = []
        scene_results: dict = {}

        for sid in sorted(scenes):
            data = scenes[sid]
            errs, warns = _validate_scene(
                data, valid_channels, valid_kinds, required_by_kind,
                all_scene_ids, rules,
            )
            scene_results[sid] = {'errors': errs, 'warnings': warns}
            all_errors.extend(f'{sid}: {e}' for e in errs)
            all_warnings.extend(f'{sid}: {w}' for w in warns)

            n_beats = len(data.get('beats') or [])
            n_choices = len(data.get('choices') or [])
            status = '[ERR]' if errs else ('[WRN]' if warns else '[OK] ')
            self.stdout.write(f'  {status} {sid:<24} {n_beats}b {n_choices}c')
            for e in errs:
                self.stdout.write(f'         ERROR: {e}')
            for w in warns:
                self.stdout.write(f'         WARN : {w}')

        # -------------------------------------------------------------------
        # Cross-scene: no_orphan_scenes
        # -------------------------------------------------------------------
        if _rule_active('no_orphan_scenes', rules) and start_scene and not options['scene']:
            adj = _build_graph(scenes)
            reachable = _reachable(adj, start_scene, all_scene_ids)
            orphans = all_scene_ids - reachable
            if orphans:
                level = _rule_level('no_orphan_scenes', rules)
                for sid in sorted(orphans):
                    msg = f'{sid}: unreachable from start_scene {start_scene!r}'
                    (all_errors if level == 'error' else all_warnings).append(msg)
                    (scene_results.setdefault(sid, {'errors': [], 'warnings': {}})
                     ['warnings' if level == 'warning' else 'errors']
                     .append('unreachable from start_scene'))

        # -------------------------------------------------------------------
        # Scene graph
        # -------------------------------------------------------------------
        if not options['no_graph'] and not options['scene']:
            self._show_graph(scenes, all_scene_ids, start_scene)

        # -------------------------------------------------------------------
        # Summary
        # -------------------------------------------------------------------
        self.stdout.write('')
        total = len(scenes)
        n_errs = len(all_errors)
        n_warns = len(all_warnings)

        if n_errs:
            self.stdout.write(self.style.ERROR(
                f'RESULT: FAIL — {n_errs} error(s), {n_warns} warning(s) across {total} scenes'
            ))
            for msg in all_errors:
                self.stdout.write(f'  ERROR: {msg}')
            sys.exit(1)
        elif n_warns and options['strict']:
            self.stdout.write(self.style.WARNING(
                f'RESULT: FAIL (strict) — 0 errors, {n_warns} warning(s)'
            ))
            for msg in all_warnings:
                self.stdout.write(f'  WARN : {msg}')
            sys.exit(1)
        elif n_warns:
            self.stdout.write(self.style.WARNING(
                f'RESULT: {total - len({s for s in scene_results if scene_results[s]["errors"]})}/'
                f'{total} OK — 0 errors, {n_warns} warning(s)  (use --strict to fail on warnings)'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'RESULT: PASS — {total}/{total} scenes valid, 0 errors, 0 warnings'
            ))

    def _show_graph(self, scenes: dict, all_scene_ids: set, start_scene):
        self.stdout.write('')
        self.stdout.write('=== Scene Graph ===')
        adj = _build_graph(scenes)

        if start_scene and start_scene in all_scene_ids:
            lines: list = []
            _print_tree(start_scene, adj, all_scene_ids, scenes, '', set(), lines)
            for line in lines:
                self.stdout.write(line)
        else:
            self.stdout.write('  (no start_scene — listing all scenes)')
            for sid in sorted(all_scene_ids):
                self.stdout.write(f'  {sid}')

        # Reachability
        if start_scene and start_scene in all_scene_ids:
            reachable = _reachable(adj, start_scene, all_scene_ids)
            unreachable = all_scene_ids - reachable
            self.stdout.write('')
            if unreachable:
                self.stdout.write(f'  Unreachable ({len(unreachable)}):')
                for sid in sorted(unreachable):
                    label = (scenes.get(sid, {}).get('title') or '')[:40]
                    self.stdout.write(f'    {sid}  {label}')
            else:
                self.stdout.write(f'  All {len(all_scene_ids)} scenes reachable from {start_scene!r}.')

        # Dead ends
        dead_ends = [
            sid for sid, data in scenes.items()
            if not data.get('next')
            and not any(
                opt.get('goto')
                for ch in (data.get('choices') or [])
                for opt in (ch.get('options') or [])
                if isinstance(opt, dict)
            )
        ]
        if dead_ends:
            self.stdout.write('')
            self.stdout.write(f'  Dead ends (no next, no goto) — {len(dead_ends)}:')
            for sid in sorted(dead_ends):
                label = (scenes.get(sid, {}).get('title') or '')[:40]
                self.stdout.write(f'    {sid}  {label}')

        # Loops
        loops = _detect_loops(adj, all_scene_ids)
        if loops:
            self.stdout.write('')
            self.stdout.write(f'  Loops detected ({len(loops)}):')
            for loop in loops:
                self.stdout.write('    ' + ' -> '.join(loop))
