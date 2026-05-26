"""
Embeds the current dispatcher_story.json into dispatcher_console.html
as the DEFAULT_STORY_DATA fallback.
"""
import json, re, sys
from pathlib import Path

BASE = Path(__file__).parent.parent / 'dispatcher_cases_v2'
HTML_PATH = BASE / 'dispatcher_console.html'
JSON_PATH = BASE / 'data' / 'dispatcher_story.json'

with open(JSON_PATH, 'r', encoding='utf-8') as f:
    story = json.load(f)

compact = json.dumps(story, ensure_ascii=False, separators=(',', ':'))

with open(HTML_PATH, 'r', encoding='utf-8') as f:
    html = f.read()

# Pattern to find and replace the DEFAULT_STORY_DATA assignment
pattern = r'(const\s+DEFAULT_STORY_DATA\s*=\s*)(\{.*?\});'
replacement = r'\g<1>' + compact.replace('\\', '\\\\') + ';'
new_html, count = re.subn(pattern, replacement, html, flags=re.DOTALL)

if count == 0:
    print("ERROR: Could not find DEFAULT_STORY_DATA in HTML", file=sys.stderr)
    sys.exit(1)

with open(HTML_PATH, 'w', encoding='utf-8') as f:
    f.write(new_html)

print("Updated DEFAULT_STORY_DATA in dispatcher_console.html")
print("  Story: " + str(len(story.get('scenes',[]))) + " scenes")
