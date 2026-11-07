from django.contrib import admin

from apps.assistant.models import AssistantQuery, LawChunk
from apps.market.admin import ReadOnlyAdmin


@admin.register(AssistantQuery)
class AssistantQueryAdmin(ReadOnlyAdmin[AssistantQuery]):
    list_display = (
        "created_at",
        "question",
        "model",
        "duration_ms",
        "completion_tokens",
        "rewritten",
        "error",
    )
    list_filter = ("model", "rewritten")
    search_fields = ("question", "answer")


@admin.register(LawChunk)
class LawChunkAdmin(ReadOnlyAdmin[LawChunk]):
    list_display = ("act", "position", "article", "heading")
    list_filter = ("act",)
    search_fields = ("article", "text")
    exclude = ("embedding",)
