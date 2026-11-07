from django.db import models
from django.utils import timezone


class LawChunk(models.Model):
    act = models.CharField(max_length=32, db_index=True)
    act_title = models.CharField(max_length=200)
    position = models.PositiveIntegerField()
    article = models.CharField(max_length=16)
    heading = models.CharField(max_length=300)
    text = models.TextField()
    embedding = models.BinaryField()

    class Meta:
        ordering = ("act", "position")
        constraints = (
            models.UniqueConstraint(fields=("act", "position"), name="assistant_chunk_position"),
        )

    def __str__(self) -> str:
        return f"{self.act} art. {self.article} #{self.position}"


class AssistantQuery(models.Model):
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    question = models.TextField()
    answer = models.TextField(blank=True)
    tool_calls = models.JSONField(default=list, blank=True)
    model = models.CharField(max_length=200)
    duration_ms = models.PositiveIntegerField(default=0)
    prompt_tokens = models.PositiveIntegerField(null=True, blank=True)
    completion_tokens = models.PositiveIntegerField(null=True, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name_plural = "assistant queries"

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.question[:60]}"
