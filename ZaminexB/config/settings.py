import sys
import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
#  Configuration
#
#  Settings are plain values edited directly in this file — there is no .env
#  and no dotenv dependency. Change them here for your environment.
# ---------------------------------------------------------------------------

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
# Generate a fresh one before deploying:
#   python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
# Production MUST set DJANGO_SECRET_KEY. The insecure fallback is local-only.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "").strip()

# SECURITY WARNING: don't run with debug turned on in production!
# Default stays on for a local checkout. Production must set DJANGO_DEBUG=0.
DEBUG = os.environ.get("DJANGO_DEBUG", "1").strip().lower() in {"1", "true", "yes", "on"}

if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "django-insecure-dev-only-not-for-production"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DEBUG is False.")

# Hosts are configurable via ALLOWED_HOSTS (comma-separated). In production
# the variable is required so a missing deploy cannot accidentally serve
# arbitrary Host headers.
#
# The local default is only reached in DEBUG. This assignment must stay the
# single source of truth: a second, unconditional `ALLOWED_HOSTS = [...]`
# below would silently discard the deployment's value and make every
# non-local request fail the Host check (and, in turn, CSRF origin checks).
DEFAULT_LOCAL_HOSTS = ["localhost", "127.0.0.1", "[::1]"]

_env_hosts = os.environ.get("ALLOWED_HOSTS", "").strip()
if _env_hosts:
    ALLOWED_HOSTS = [h.strip() for h in _env_hosts.split(",") if h.strip()]
elif DEBUG:
    ALLOWED_HOSTS = list(DEFAULT_LOCAL_HOSTS)
else:
    raise ImproperlyConfigured(
        "ALLOWED_HOSTS must be set when DEBUG is False (comma-separated list)."
    )

# Add project root to path (safer than adding apps/ directly)
# Rationale: INSTALLED_APPS uses package paths like 'apps.accounts...'.
# Pointing sys.path at BASE_DIR ensures Python can import the top-level
# 'apps' package without shadowing it by injecting BASE_DIR/apps directly.
sys.path.insert(0, str(BASE_DIR))


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.postgres',
    'apps.common.apps.CommonConfig',
    'apps.basics.apps.BasicsConfig',
    'apps.accounts.apps.AccountsConfig',
    'apps.listings.apps.ListingsConfig',
    'apps.properties.apps.PropertiesConfig',
    'apps.tasks.apps.TasksConfig',
    'apps.followups.apps.FollowupsConfig',
    'apps.tickets.apps.TicketsConfig',
    'apps.reports.apps.ReportsConfig',
    'apps.analytics.apps.AnalyticsConfig',
    'apps.activity.apps.ActivityConfig',
    'apps.notifications.apps.NotificationsConfig',
    'rest_framework',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'apps.accounts.middleware.ArchivedConsultantSessionMiddleware',
    'apps.common.middleware.CurrentUserMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'apps.common.middleware.SecurityHeadersMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / "templates"],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.0/ref/settings/#databases

# PostgreSQL is the only supported backend. The schema relies on JSONB, partial
# indexes and typed EAV columns, and the reference-data tables assume
# PostgreSQL semantics.
#
# Edit the values below to match your server. A DATABASE_URL environment
# variable, when present, still wins — that is what deployment platforms set —
# but nothing is required for a normal local checkout.
DATABASE_NAME = "zaminex"
DATABASE_USER = "zaminex"
DATABASE_PASSWORD = "zaminex"
DATABASE_HOST = "localhost"
DATABASE_PORT = "5432"

if os.environ.get("DATABASE_URL"):
    DATABASES = {
        "default": dj_database_url.parse(
            os.environ["DATABASE_URL"],
            conn_max_age=600,
            conn_health_checks=True,
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": DATABASE_NAME,
            "USER": DATABASE_USER,
            "PASSWORD": DATABASE_PASSWORD,
            "HOST": DATABASE_HOST,
            "PORT": DATABASE_PORT,
            # Re-use connections for 10 minutes instead of opening one per
            # request; a meaningful win on PostgreSQL.
            "CONN_MAX_AGE": 600,
            "CONN_HEALTH_CHECKS": True,
        }
    }

# Fail loudly rather than silently running on an unsupported backend.
if "postgresql" not in DATABASES["default"]["ENGINE"]:
    raise ImproperlyConfigured(
        "Zaminex requires PostgreSQL, but the configured engine is "
        f"'{DATABASES['default']['ENGINE']}'. Check the DATABASE_* values in "
        "config/settings.py."
    )

# Tests build a throwaway database, so behaviour that differs between backends
# (case-insensitive search, JSON handling, constraint enforcement) is exercised
# exactly as in production.
if "test" in sys.argv:
    DATABASES["default"].setdefault("TEST", {})
    DATABASES["default"]["TEST"]["NAME"] = "test_zaminex"


# Password validation
# https://docs.djangoproject.com/en/5.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.0/topics/i18n/

LANGUAGE_CODE = 'fa-ir'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.0/howto/static-files/

STATIC_URL = 'static/'

STATICFILES_DIRS = [
    BASE_DIR / "static",
]

STATIC_ROOT = BASE_DIR / "staticfiles"

# Default primary key field type
# https://docs.djangoproject.com/en/5.0/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Task 3: use custom user model
AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"

LOGOUT_REDIRECT_URL = "/accounts/login/"

# Task 4: media settings
MEDIA_URL = "/media/"


def _resolve_media_root(value: str, base: Path) -> Path:
    """Turn the optional ``MEDIA_ROOT`` environment value into a real path.

    A relative value is resolved against the project directory, never against
    the process working directory, so the upload tree cannot silently move when
    the server is started from another folder.
    """

    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else (base / candidate).resolve()


# Uploads have to outlive the code that points at them.
#
# The default keeps the tree inside the checkout so a plain clone works with no
# configuration at all. A deployment that resets its working tree, however —
# ``git clean -fd``, ``git checkout -f``, a fresh clone, a rebuilt container
# image — then deletes every uploaded file while its database row survives, and
# the download endpoint answers 404 «فایل یافت نشد.» forever after. That is
# exactly the "پیوست تیکت دانلود نمی‌شود" bug. ``.gitignore`` already keeps the
# upload tree out of version control so ``git clean -fd`` skips it; point
# MEDIA_ROOT at a persistent path outside the repository as well when the
# checkout itself is disposable:
#
#     export MEDIA_ROOT=/var/lib/zaminex/media
#
_env_media_root = os.environ.get("MEDIA_ROOT", "").strip()
MEDIA_ROOT = (
    _resolve_media_root(_env_media_root, BASE_DIR)
    if _env_media_root
    else BASE_DIR / "media"
)

# Created eagerly so a freshly mounted volume accepts the first upload instead
# of failing with FileNotFoundError, and a mis-typed path surfaces at start-up
# rather than at somebody's first download.
os.makedirs(MEDIA_ROOT, exist_ok=True)

# Account-scoped login protection: 5 failed attempts in 15 minutes => 10-minute lock.
LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
LOGIN_LOCKOUT_SECONDS = 10 * 60

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


# ---------------------------------------------------------------------------
#  Cache (Phase 2)
#
#  Redis is an optimisation, never a dependency (roadmap principles):
#  * ``REDIS_URL`` set  → django-redis is the default cache backend;
#  * ``REDIS_URL`` absent → in-process LocMem — the project's "local with no
#    dependencies" philosophy is preserved for plain checkouts;
#  * ``IGNORE_EXCEPTIONS: True`` (fail-open): a dead or slow Redis degrades
#    to a cache miss, never to a 500. The short socket timeouts cap how long
#    a hanging Redis can stall a request.
#
#  Everything that reads this cache (DRF's throttle counters, and the Phase
#  3+ cache consumers) inherits the fail-open behaviour — no consumer code
#  has to handle cache errors.
# ---------------------------------------------------------------------------
def _cache_settings():
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if redis_url:
        return {
            "default": {
                "BACKEND": "django_redis.cache.RedisCache",
                "LOCATION": redis_url,
                "OPTIONS": {
                    "CLIENT_CLASS": "django_redis.client.DefaultClient",
                    # Plain JSON text in Redis — inspectable in redis-cli,
                    # no pickle. Every consumer stores strings (cache_utils
                    # payloads, DRF throttle counters), so the JSON
                    # serializer is lossless for this app.
                    "SERIALIZER": "django_redis.serializers.json.JSONSerializer",
                    # Fail-open: swallow backend errors as misses. The
                    # errors are still recorded — see
                    # DJANGO_REDIS_LOG_IGNORED_EXCEPTIONS below; failing
                    # open must not also mean failing silently.
                    "IGNORE_EXCEPTIONS": True,
                    # A hung Redis must not stall requests: bound the connect
                    # and read windows tightly. The cost is per operation, not
                    # per request — a warm request makes 5 cache round trips
                    # and a cold one up to 9 — so this value is the multiplier
                    # on the worst case. Measured against a Redis that accepts
                    # the connection and never replies:
                    #     0.5s → 4,529 ms per cold request
                    #     0.1s →   928 ms per cold request
                    # Local Redis answers in well under a millisecond, so
                    # 0.1s is ~100x headroom and still fails fast.
                    "SOCKET_CONNECT_TIMEOUT": 0.1,
                    "SOCKET_TIMEOUT": 0.1,
                },
            }
        }
    return {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "zaminex-default",
        }
    }


CACHES = _cache_settings()

# django-redis reads this as a top-level setting, not from OPTIONS. Without
# it ``IGNORE_EXCEPTIONS`` swallows every backend error in silence: a dead or
# hung Redis showed up only as slow responses and (before the throttle
# fallback in apps/common/throttles.py) as rate limits quietly switching
# themselves off — no 500, no warning, nothing to grep for. The underlying
# exception is what distinguishes "connection refused" from "read timed out"
# from "OOM command not allowed", so it is worth the log volume during an
# outage; apps.common.cache_utils additionally logs a single, unambiguous
# warning each time the backend transitions to down.
DJANGO_REDIS_LOG_IGNORED_EXCEPTIONS = True

# ---------------------------------------------------------------------------
#  Geocoding (map place search)
# ---------------------------------------------------------------------------
# The map picker no longer calls a public geocoder from the browser: that was
# blocked by the app's own Content-Security-Policy and could neither be cached
# nor rate-limited. Requests now go to /common/api/geocode/ and leave the
# server from a single place (apps/common/geocode.py).
#
# GEOCODE_UPSTREAM is an environment variable rather than a constant so a
# self-hosted Nominatim can be swapped in with no code change — which is also
# what makes a deployment with no internet access possible.
GEOCODE_UPSTREAM = os.environ.get(
    "GEOCODE_UPSTREAM", "https://nominatim.openstreetmap.org/search"
).strip()
# Identify ourselves: the public Nominatim instance's usage policy requires a
# descriptive User-Agent, and an anonymous client is the first to be throttled.
GEOCODE_USER_AGENT = os.environ.get(
    "GEOCODE_USER_AGENT",
    "Zaminex-CRM/1.0 (+self-hosted real-estate CRM; geocode proxy)",
).strip()
# Kept short on purpose: a geocode lookup is interactive, and a hung upstream
# must not hold a request open the way the AI call (60 s) legitimately does.
GEOCODE_TIMEOUT = float(os.environ.get("GEOCODE_TIMEOUT", "8"))
GEOCODE_MAX_QUERY_LENGTH = 200
GEOCODE_LIMIT = 1
# The public instance allows ~1 request/second per client. Paced server-side
# through the shared cache, so the budget is global across workers when Redis
# is present and per-process otherwise.
GEOCODE_PACING_SECONDS = float(os.environ.get("GEOCODE_PACING_SECONDS", "1.1"))
# A place's coordinates are effectively permanent, so a hit is cached for a
# month; a miss far less, because OpenStreetMap's coverage improves and a
# stale "not found" must not outlive the fix.
GEOCODE_CACHE_TTL = 30 * 24 * 3600
GEOCODE_NEGATIVE_CACHE_TTL = 7 * 24 * 3600

REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "apps.common.exceptions.persian_exception_handler",
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        # Resilient wrappers: the stock DRF classes read their history with
        # ``cache.get(key, [])``, which with IGNORE_EXCEPTIONS cannot tell an
        # empty counter from a dead backend — so a Redis outage would turn
        # every rate limit off. See apps/common/throttles.py.
        "apps.common.throttles.ResilientAnonRateThrottle",
        "apps.common.throttles.ResilientUserRateThrottle",
        "apps.common.throttles.ResilientScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        # These two apply to every DRF view, because DEFAULT_THROTTLE_CLASSES
        # includes AnonRateThrottle and UserRateThrottle and a view that does
        # not declare its own throttle_classes inherits them. So an endpoint
        # with no explicit scope is not unthrottled — it is covered here.
        "anon": "60/min",
        "user": "300/min",
        # Tighter per-endpoint scopes. Each one is only in force on a view
        # that names it via `throttle_scope`; a rate declared here with no
        # such view protects nothing while reading like it does, which is
        # worse than not declaring it. (test_throttles.py walks the URLconf
        # and fails if one goes unused.)
        "password_reset": "5/hour",
        "ai": "10/hour",
        # A geocode lookup fans out into up to three upstream calls (the query
        # ladder), so this is generous for a human but still bounds a runaway.
        "geocode": "60/min",
    },
}

if "test" in sys.argv:
    REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = []

# ---------------------------------------------------------------------------
# File upload hardening
# ---------------------------------------------------------------------------
# Cap request body sizes to make bulk upload attacks impractical. These are
# deliberately above the legitimate property image size (5 MB per file, up to
# 10 files per request) so the UI keeps working.
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10 MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024  # 12 MB
DATA_UPLOAD_MAX_NUMBER_FIELDS = 1000

# ---------------------------------------------------------------------------
# Session hardening
# ---------------------------------------------------------------------------
# Idle timeout: the user is logged out after 12 hours of inactivity. A
# persistent cap of 7 days prevents a stolen cookie from living forever.
SESSION_COOKIE_AGE = 12 * 60 * 60  # 12 hours
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

# Phase 6: the session table gets the same treatment as every other hot read —
# the cache goes in front of it. ``cached_db`` reads the session from the
# cache and only touches ``django_session`` on a miss (and always persists the
# write to the table), so:
#   * a warm request saves the session SELECT (one fewer DB round trip — the
#     point of the phase, since SESSION_SAVE_EVERY_REQUEST already writes on
#     every request),
#   * a cache flush can never kill a logged-in session: the row stays in the
#     table and the next read reloads it,
#   * fail-open: with the Phase-2 CACHES config (IGNORE_EXCEPTIONS=True) a dead
#     Redis presents as "always a miss / no-op writes", and the store then
#     degrades transparently to the plain DB engine — no 500s on login,
#     requests or logout (the store also wraps its own cache calls).
# No migration: cached_db uses the same django_session table.
#
# The subclass adds one thing: it skips the cache while cache_utils has
# marked the backend down. Fail-open already made an outage *correct* here,
# but not *cheap* — with SESSION_SAVE_EVERY_REQUEST on, every authenticated
# request still paid two socket timeouts on a hung Redis. Skipping them
# degrades to the plain DB engine immediately instead. See
# apps/common/session_backend.py.
# SESSION_ENGINE names the *module* — Django imports it and uses its
# ``SessionStore`` attribute, the same contract the stock engines follow.
SESSION_ENGINE = "apps.common.session_backend"
# CSRF cookie must be readable by JS to set the X-CSRFToken header from the
# SPA; keep it scoped to the same site and HttpOnly off (this is standard
# for a session-authenticated SPA).
CSRF_COOKIE_HTTPONLY = False

# ---------------------------------------------------------------------------
# CSRF trusted origins
# ---------------------------------------------------------------------------
# Django >= 4.0 validates the `Origin` header of every unsafe request against
# this list (falling back to the Host header only when no Origin is sent).
# Behind a reverse proxy the browser's Origin carries the *public* scheme and
# host, which is not necessarily what Django reconstructs from the request, so
# the list has to be explicit.
#
# `CSRF_TRUSTED_ORIGINS` (comma-separated, each entry including a scheme) is
# the authoritative source. On top of it, every entry of ALLOWED_HOSTS is
# registered automatically: an operator who correctly declares their hosts
# should not additionally have to restate them here to be able to log in.
# Wildcard hosts ("*" and ".example.com") are passed through in the form
# Django expects, and "*" alone is skipped because it is not a valid origin.
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]


def _origins_for_host(host: str) -> list[str]:
    """Both schemes for one ALLOWED_HOSTS entry, in CSRF_TRUSTED_ORIGINS form."""
    host = host.strip()
    # "*" matches any host; there is no origin that expresses that, and
    # inventing one would trust every site on the internet.
    if not host or host == "*":
        return []
    # Django already accepts a leading dot as "this domain and subdomains";
    # the origin form of that is "https://*.example.com".
    if host.startswith("."):
        host = f"*{host}"
    return [f"https://{host}", f"http://{host}"]


def _with_host_origins(configured: list[str], hosts: list[str]) -> list[str]:
    """`configured` plus an origin for every host, without duplicates.

    Written as a function so the loop variables cannot leak into the module
    namespace and be picked up as settings.
    """
    origins = list(configured)
    for host in hosts:
        for origin in _origins_for_host(host):
            if origin not in origins:
                origins.append(origin)
    return origins


CSRF_TRUSTED_ORIGINS = _with_host_origins(CSRF_TRUSTED_ORIGINS, ALLOWED_HOSTS)

# Under the test runner Django appends "testserver" to ALLOWED_HOSTS at
# *runtime* (django.test.utils.setup_test_environment) — after this module
# has already derived CSRF_TRUSTED_ORIGINS from ALLOWED_HOSTS above. That
# late injection would leave "testserver" as the one allowed host without a
# matching trusted origin, breaking the invariant that every allowed host
# can pass the CSRF Origin check. Mirror the injection here so the derived
# origins stay complete. Test-runner only: production origins are unchanged.
if sys.argv[1:2] == ["test"]:
    CSRF_TRUSTED_ORIGINS = _with_host_origins(CSRF_TRUSTED_ORIGINS, ["testserver"])
