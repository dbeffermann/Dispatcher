"""
Shared YAML parsing utilities for the Studio management commands and scripts.

Layer:   Utility — shared by import_yaml, validate_yaml, and external scripts.
Stdlib:  No PyYAML dependency. Works anywhere Python 3.8+ is available.

=========================================================================
SUPPORTED YAML SUBSET (intentionally restricted)
=========================================================================
The parser handles the controlled subset produced by export_yaml and used
in hand-authored profile/schema files. Unsupported constructs cause silent
failures or incorrect parsing, so authoring must stay within this subset.

SUPPORTED:
  Mappings (block style):
      key: value
      key:
        nested_key: value

  Sequences (block style with leading dash):
      - item_string
      - key: value     # inline mapping start, continuation is indented
        more_key: val

  Scalar types:
      Unquoted string   ->  "hello world"   (str)
      Single-quoted     ->  'hello'         (str, no escape sequences)
      Double-quoted     ->  "hello"         (str, supports \", \n, \\)
      Integer           ->  42              (int)
      Boolean           ->  true / false    (bool)
      Null              ->  null / (empty)  (None)
      Empty list        ->  []              (list)
      Empty dict        ->  {}              (dict)

  Comments:
      # Any line whose stripped content starts with '#' is ignored.
      Inline comments after values:  key: value  # comment  (stripped)

NOT SUPPORTED (will cause incorrect results):
  - Multi-line strings (| and > block scalars)
  - Inline flow collections spanning multiple lines
  - Inline flow collections with content: [a, b, c] or {k: v}
    (EXCEPTION: empty [] and {} are supported as a special case)
  - Anchors (&) and aliases (*)
  - YAML tags (!!)
  - Multi-document files (--- separator)
  - Implicit octal/hex/float scalars
  - Nested inline quotes within block scalars

DESIGN RATIONALE:
  export_yaml produces block-style YAML only. Profile and schema files are
  written by hand following the same convention. Restricting to this subset
  keeps the parser small (~80 lines), dependency-free, and auditable.
  If you need full YAML support, install PyYAML and replace load_yaml_file.
=========================================================================

Usage:
    from dispatcher_authoring.yaml_io import load_yaml_file, parse_yaml
"""
from pathlib import Path


# ---------------------------------------------------------------------------
# Parser internals
# ---------------------------------------------------------------------------

def _count_indent(line: str) -> int:
    return len(line) - len(line.lstrip(' '))


def _parse_scalar(raw: str):
    """Parse a scalar string value into the appropriate Python type."""
    s = raw.strip()
    if not s or s == 'null':
        return None
    if s == 'true':
        return True
    if s == 'false':
        return False
    # Inline flow collections (minimal support: empty only)
    if s == '[]':
        return []
    if s == '{}':
        return {}
    try:
        return int(s)
    except ValueError:
        pass
    # Quoted string (single or double)
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        inner = s[1:-1]
        inner = inner.replace('\\"', '"').replace("\\n", "\n").replace('\\\\', '\\')
        return inner
    return s


def _parse_block(lines: list, start: int, parent_indent: int):
    """
    Parse a YAML block starting at `start`.
    Returns (value, next_line_index).
    """
    i = start
    while i < len(lines) and (not lines[i].strip() or lines[i].strip().startswith('#')):
        i += 1

    if i >= len(lines):
        return None, i

    first = lines[i]
    indent = _count_indent(first)

    # List block
    if first.lstrip().startswith('- ') or first.strip() == '-':
        result = []
        while i < len(lines):
            line = lines[i]
            if not line.strip() or line.strip().startswith('#'):
                i += 1
                continue
            cur_indent = _count_indent(line)
            if cur_indent < indent:
                break
            if cur_indent > indent:
                i += 1
                continue
            stripped = line.lstrip()
            if not stripped.startswith('-'):
                break
            after_dash = stripped[1:].lstrip() if len(stripped) > 1 else ''
            if not after_dash:
                item, i = _parse_block(lines, i + 1, indent)
                result.append(item)
            elif ':' in after_dash and not after_dash.startswith('"') and not after_dash.startswith("'"):
                # Inline mapping start: collect continuation lines
                item_lines = [' ' * (indent + 2) + after_dash]
                j = i + 1
                while j < len(lines):
                    nline = lines[j]
                    if not nline.strip() or nline.strip().startswith('#'):
                        j += 1
                        continue
                    nindent = _count_indent(nline)
                    if nindent <= indent:
                        break
                    item_lines.append(nline)
                    j += 1
                item, _ = _parse_block(item_lines, 0, indent + 1)
                result.append(item)
                i = j
            else:
                result.append(_parse_scalar(after_dash))
                i += 1
        return result, i

    # Mapping block
    if ':' in first.lstrip() and not first.lstrip().startswith('-'):
        result = {}
        while i < len(lines):
            line = lines[i]
            if not line.strip() or line.strip().startswith('#'):
                i += 1
                continue
            cur_indent = _count_indent(line)
            if cur_indent < indent:
                break
            if cur_indent > indent:
                i += 1
                continue
            stripped = line.lstrip()
            if stripped.startswith('-'):
                break
            colon_pos = stripped.find(':')
            if colon_pos < 0:
                i += 1
                continue
            key = stripped[:colon_pos].strip()
            after_colon = stripped[colon_pos + 1:].lstrip()
            if not after_colon or after_colon.startswith('#'):
                val, i = _parse_block(lines, i + 1, indent)
                result[key] = val
            else:
                result[key] = _parse_scalar(after_colon.split('#')[0].rstrip())
                i += 1
        return result, i

    # Plain scalar
    return _parse_scalar(first.lstrip()), i + 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_yaml(text: str):
    """
    Parse a YAML string and return the root value (usually a dict).
    Returns an empty dict on empty input.
    """
    lines = text.splitlines()
    result, _ = _parse_block(lines, 0, -1)
    return result if result is not None else {}


def load_yaml_file(path: Path) -> dict:
    """
    Load a YAML file and return a dict.
    Raises ValueError on parse failure (caller can catch or re-raise as CommandError).
    """
    try:
        text = path.read_text(encoding='utf-8')
        result = parse_yaml(text)
        return result if isinstance(result, dict) else {}
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f'Failed to parse {path}: {e}') from e
