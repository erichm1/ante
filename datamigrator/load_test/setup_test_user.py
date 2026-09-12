"""
Creates (or resets) a dedicated load-test Django user so the test doesn't
depend on the real admin credentials.

Run once before the load test:
  python load_test/setup_test_user.py

The user is created as staff=False, is_superuser=False — just enough to pass
LoginRequiredMiddleware (is_authenticated=True).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "datamigrator.settings")

import django
django.setup()

from django.contrib.auth.models import User

USERNAME = "loadtest"
PASSWORD = "loadtest123!"

user, created = User.objects.get_or_create(username=USERNAME)
user.set_password(PASSWORD)
user.is_active = True
user.save()

action = "Created" if created else "Reset"
print(f"{action} user '{USERNAME}' with password '{PASSWORD}'")
