from django.db import migrations


def seed_file_integrations(apps, schema_editor):
    Integration = apps.get_model("integrations", "Integration")
    Integration.objects.get_or_create(
        slug="csv-file",
        defaults=dict(
            name="CSV File",
            description="Use an uploaded CSV file as a migration source — no API or credentials needed.",
            category="FILE", icon="bi-filetype-csv",
            auth_type="none", is_file_based=True, is_featured=True,
        ),
    )
    Integration.objects.get_or_create(
        slug="xlsx-file",
        defaults=dict(
            name="Excel (XLSX) File",
            description="Use an uploaded .xlsx workbook as a migration source — no API or credentials needed.",
            category="FILE", icon="bi-filetype-xlsx",
            auth_type="none", is_file_based=True, is_featured=True,
        ),
    )


def remove_file_integrations(apps, schema_editor):
    Integration = apps.get_model("integrations", "Integration")
    Integration.objects.filter(slug__in=["csv-file", "xlsx-file"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0004_integration_is_file_based_alter_integration_category"),
    ]

    operations = [
        migrations.RunPython(seed_file_integrations, remove_file_integrations),
    ]
