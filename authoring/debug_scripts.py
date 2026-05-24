import os
import django
import re

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "authoring.settings")
django.setup()

from django.template.loader import render_to_string
from django.test import RequestFactory
from django.contrib.auth.models import User
from dispatcher_authoring.models import GameProject
from dispatcher_authoring.views import _build_story_map_context

factory = RequestFactory()
request = factory.get("/studio/story-map/")
request.user = User.objects.first()

ctx = _build_story_map_context(request)
result = render_to_string("dispatcher_authoring/studio_story_map.html", ctx, request=request)

scripts = re.findall(r"<script[^>]*>(.*?)</script>", result, re.DOTALL)
for i, s in enumerate(scripts):
    bt_count = s.count("`")
    sq_count = s.count("'")
    dq_count = s.count("\"")
    print(f"Script {i}: length={len(s)}, bt={bt_count}, sq={sq_count}, dq={dq_count}")
    if bt_count % 2 != 0:
        indices = [idx for idx, char in enumerate(s) if char == "`"]
        print(f"  ODD BACKTICKS in Script {i}! Last backtick index: {indices[-1]}")
        last_idx = indices[-1]
        print("  Snippet around last backtick:")
        print(s[max(0, last_idx-200):min(len(s), last_idx+200)])
