import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "insecure-dev-key-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "True") == "True"
ALLOWED_HOSTS = ["*"]

# Encrypts connection credentials (tokens, api keys, passwords) at rest.
# Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FIELD_ENCRYPTION_KEY = os.environ.get(
    "FIELD_ENCRYPTION_KEY", "JGx7l9y5S8n1v4H3z6r0m2Q8w1e4T7u9I2o5P8a1S4c="
)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_filters",
    "accounts",
    "home",
    "connections",
    "integrations",
    "schemas",
    "mappings",
    "jobs",
    "plans",
    "chains",
    "studio",
    "notifications",
    "reports",
    "incidents",
    "tickets",
    "attachments",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",         # reads the django_language cookie the language picker sets
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "accounts.middleware.ModuleAccessMiddleware",      # after messages: a denial is shown as a warning message
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "datamigrator.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.profile",
                "accounts.context_processors.access",
            ],
        },
    },
]

WSGI_APPLICATION = "datamigrator.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        # A migration run now writes its progress from a background thread while
        # the canvas polls it via GET — bump SQLite's busy-wait timeout so that
        # overlap doesn't surface as "database is locked". For real concurrent
        # load, move to Postgres instead of raising this further.
        "OPTIONS": {"timeout": 20},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
# The UI languages (kept in step with LANGS in static/js/i18n.js). Django translates its own dates, form
# widgets and validation messages for these; the app's own text is translated in the browser (see i18n/).
LANGUAGES = [
    ("en", "English"), ("pt-br", "Português"), ("es", "Español"), ("fr", "Français"),
    ("de", "Deutsch"), ("it", "Italiano"), ("ja", "日本語"), ("zh-hans", "中文"),
]
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Uploaded integration logos (Integration.icon_image) live here.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
}

# Accounts are created by an administrator (Profile → Administration → Users), never by the visitor: only an
# account that already exists can sign in. Set ALLOW_SELF_REGISTRATION=True to bring back the open sign-up page.
ALLOW_SELF_REGISTRATION = os.environ.get("ALLOW_SELF_REGISTRATION", "False") == "True"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "home:index"
LOGOUT_REDIRECT_URL = "landing"

MESSAGE_TAGS = {}
