from django.db import migrations

# Frozen copy of accounts.permissions.STANDARD_GROUP_MODULES at the time permissions were introduced: what an
# ordinary user could already reach beforehand (Incidents was staff-only, and staff keep everything anyway).
STANDARD_MODULES = ["status", "appstore", "mappings", "jobs", "plans", "chains", "studio", "reports", "tickets", "logs"]


def create_default_group(apps, schema_editor):
    """Introducing permissions must not lock anyone out: create the default group and put every existing
    non-administrator in it. (Administrators — is_staff/is_superuser — always have every module.)"""
    AccessGroup = apps.get_model("accounts", "AccessGroup")
    Profile = apps.get_model("accounts", "Profile")
    User = apps.get_model("auth", "User")

    group, _ = AccessGroup.objects.get_or_create(
        name="Standard user",
        defaults={"description": "Everything an ordinary user could use before permissions existed.",
                  "modules": STANDARD_MODULES, "is_default": True},
    )
    for user in User.objects.filter(is_staff=False, is_superuser=False):
        profile, _ = Profile.objects.get_or_create(user=user)
        profile.groups.add(group)


def remove_default_group(apps, schema_editor):
    apps.get_model("accounts", "AccessGroup").objects.filter(name="Standard user").delete()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_departments_groups_permissions"), ("auth", "0012_alter_user_first_name_max_length")]
    operations = [migrations.RunPython(create_default_group, remove_default_group)]
