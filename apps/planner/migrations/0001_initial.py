import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Appliance",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("key", models.SlugField(blank=True, default="", max_length=40)),
                ("name", models.CharField(max_length=80)),
                ("duration_minutes", models.PositiveIntegerField()),
                ("power_kw", models.DecimalField(decimal_places=2, max_digits=6)),
                ("is_interruptible", models.BooleanField(default=False)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="appliances",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("sort_order", "name"),
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("user__isnull", True)),
                        fields=("key",),
                        name="planner_unique_template_key",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("user__isnull", False)),
                        fields=("user", "name"),
                        name="planner_unique_user_appliance_name",
                    ),
                ],
            },
        ),
    ]
