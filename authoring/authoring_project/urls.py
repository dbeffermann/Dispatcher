from django.contrib import admin
from django.urls import path
from django.contrib.admin.views.decorators import staff_member_required

from dispatcher_authoring.views import (
    story_map_view,
    studio_story_map_view,
    export_now_view,
    scene_api_view,
    scene_create_view,
    beat_create_view,
    beat_api_view,
    beat_transfer_view,
    beats_reorder_view,
    scene_choice_create_view,
    choice_api_view,
    choice_option_create_view,
    choice_option_api_view,
    vignettes_api_view,
    scenes_list_api_view,
    asset_file_view,
    case_field_create_view,
)

urlpatterns = [
    # Custom views — must come before admin.site.urls
    path('admin/dispatcher_authoring/story-map/', staff_member_required(story_map_view), name='dispatcher_story_map'),
    path('studio/story-map/', staff_member_required(studio_story_map_view), name='dispatcher_studio_map'),
    path('studio/export/', staff_member_required(export_now_view), name='dispatcher_export_now'),

    # Studio REST API
    path('studio/api/scene/<int:pk>/', staff_member_required(scene_api_view), name='studio_scene_api'),
    path('studio/api/scene/', staff_member_required(scene_create_view), name='studio_scene_create'),
    path('studio/api/scene/<int:scene_pk>/beat/', staff_member_required(beat_create_view), name='studio_beat_create'),
    path('studio/api/scene/<int:scene_pk>/choice/', staff_member_required(scene_choice_create_view), name='studio_choice_create'),
    path('studio/api/beat/<int:pk>/', staff_member_required(beat_api_view), name='studio_beat_api'),
    path('studio/api/beat/<int:pk>/transfer/', staff_member_required(beat_transfer_view), name='studio_beat_transfer'),
    path('studio/api/choice/<int:pk>/', staff_member_required(choice_api_view), name='studio_choice_api'),
    path('studio/api/choice-option/<int:pk>/', staff_member_required(choice_option_api_view), name='studio_choice_option_api'),
    path('studio/api/choice/<int:choice_pk>/option/', staff_member_required(choice_option_create_view), name='studio_choice_option_create'),
    path('studio/api/scene/<int:scene_pk>/beats/reorder/', staff_member_required(beats_reorder_view), name='studio_beats_reorder'),
    path('studio/api/case/<int:case_pk>/field/', staff_member_required(case_field_create_view), name='studio_case_field_create'),
    path('studio/api/vignettes/', staff_member_required(vignettes_api_view), name='studio_vignettes_api'),
    path('studio/api/scenes/', staff_member_required(scenes_list_api_view), name='studio_scenes_api'),
    path('studio/asset-file/<path:asset_path>/', staff_member_required(asset_file_view), name='studio_asset_file'),

    path('admin/', admin.site.urls),
]
