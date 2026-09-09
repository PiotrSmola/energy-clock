from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

from apps.planner.catalog import SYSTEM_TEMPLATES


def seed_templates(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    Appliance = apps.get_model("planner", "Appliance")
    for position, template in enumerate(SYSTEM_TEMPLATES):
        Appliance.objects.update_or_create(
            user=None,
            key=template.key,
            defaults={
                "name": template.name,
                "duration_minutes": template.duration_minutes,
                "power_kw": template.power_kw,
                "is_interruptible": template.is_interruptible,
                "sort_order": position,
            },
        )


def remove_templates(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    Appliance = apps.get_model("planner", "Appliance")
    Appliance.objects.filter(
        user=None, key__in=[template.key for template in SYSTEM_TEMPLATES]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("planner", "0001_initial")]

    operations = [migrations.RunPython(seed_templates, remove_templates)]
