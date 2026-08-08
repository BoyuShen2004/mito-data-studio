"""
Django settings for the Mito Data Agent project.

Mito Data Agent is a web application for managing mitochondria annotation work:
projects, image volumes, frame-based annotation tasks, submissions, review, and
workload tracking. Annotation work is unpaid; there is no payment tracking.
"""

import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from a local .env file at the repo root if present.
load_dotenv(BASE_DIR.parent / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# A deployment chooses one coherent compatibility posture before individual
# feature flags are considered. ``production_integrated_v1`` is the audited
# v1.1 release posture: mature workflow/durability features and the validated
# pyramid/chunk transport on, with the original slice path retained as fallback.
# Unlike the broad
# ``webknossos`` development profile, startup checks reject per-flag drift from
# this production contract.
MITO_UPGRADE_PROFILE = os.getenv("MITO_UPGRADE_PROFILE", "legacy").strip().lower()
if MITO_UPGRADE_PROFILE not in {
    "legacy",
    "webknossos",
    "production_integrated_v1",
}:
    raise ImproperlyConfigured(
        "MITO_UPGRADE_PROFILE must be 'legacy', 'webknossos', or "
        "'production_integrated_v1'."
    )


PRODUCTION_INTEGRATED_FEATURES = {
    "FEATURE_TEAMS": True,
    "FEATURE_AUTO_FILL_SCHEDULER": True,
    "FEATURE_REVIEW_HISTORY": True,
    "FEATURE_DASHBOARDS": True,
    "FEATURE_ANNOTATION_OPS": True,
    "FEATURE_INTERPOLATION": True,
    "FEATURE_ANNOTATION_TOOLS": True,
    # Additive Zarr derivatives accelerate reads only; TIFF/HDF5/NIfTI sources
    # and the existing label-write path remain authoritative and available.
    "FEATURE_VOLUME_PYRAMIDS": True,
    "FEATURE_CHUNK_SERVICE": True,
}


def _upgrade_feature(name: str) -> bool:
    if MITO_UPGRADE_PROFILE == "production_integrated_v1":
        default = PRODUCTION_INTEGRATED_FEATURES[name]
    else:
        default = MITO_UPGRADE_PROFILE == "webknossos"
    return _env_bool(name, default)


# Are we running the test suite? Used only to pick safer *defaults* — every
# value it influences stays overridable, so a test run never silently diverges
# from how development behaves. `sys.argv[1:2]` matches `manage.py test` without
# matching an app or label that merely happens to be called "test".
RUNNING_TESTS = (
    sys.argv[1:2] == ["test"]
    or os.getenv("PYTEST_CURRENT_TEST") is not None
    or _env_bool("MITO_TEST_MODE")
)


# --- Core security / debug -------------------------------------------------

SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "django-insecure-)xbnet#ko+0&(934o5j80-fr@w*v4pk6ctrap2fyn1tj412q(e",
)

DEBUG = _env_bool("DJANGO_DEBUG", True)

# Passwordless, confirmation-only wipe for the login-page Development accounts
# panel. It is still inert unless ENABLE_MOCK_DEV_LOGIN and a non-empty account
# allowlist are also configured. Leave off for locked-down production/valuable
# data; explicitly disposable demo deployments may enable it without DEBUG.
MITO_ALLOW_DEV_RESET = _env_bool("MITO_ALLOW_DEV_RESET", False)

# Safe maintenance controls. Mock login is an explicit server-side allow-list;
# production keeps it off. The separate production administrator reset requires
# a verified backup marker and an operator-controlled write-freeze window.
ENABLE_MOCK_DEV_LOGIN = _env_bool("ENABLE_MOCK_DEV_LOGIN", False)
MOCK_DEV_LOGIN_ACCOUNTS = tuple(
    value.strip()
    for value in os.getenv("MOCK_DEV_LOGIN_ACCOUNTS", "").split(",")
    if value.strip()
)
# The click-to-fill credential is deployment configuration, never a literal in
# the SPA source. These accounts must contain no valuable or private data.
MOCK_DEV_LOGIN_PASSWORD = os.getenv("MOCK_DEV_LOGIN_PASSWORD", "")
MITO_MAINTENANCE_MODE = _env_bool("MITO_MAINTENANCE_MODE", False)
MITO_RESET_BACKUP_MARKER = os.getenv("MITO_RESET_BACKUP_MARKER", "").strip()
MITO_RESET_BACKUP_MAX_AGE_SECONDS = int(
    os.getenv("MITO_RESET_BACKUP_MAX_AGE_SECONDS", "86400")
)
MITO_RESET_ADMIN_USERNAME = os.getenv("MITO_RESET_ADMIN_USERNAME", "admin").strip()

ALLOWED_HOSTS = [
    h.strip()
    for h in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]


# --- Application definition ------------------------------------------------

INSTALLED_APPS = [
    # Manager Admin site (replaces the default django.contrib.admin site).
    "core.admin_apps.ManagerAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "rest_framework.authtoken",
    "corsheaders",
    # Local apps
    "core",
    "accounts",
    "projects",
    "volumes",
    "annotation",
    "processing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "core.observability.RequestObservabilityMiddleware",
    # Serves the built frontend (frontend/dist) and STATIC_ROOT directly from
    # the WSGI process — no-op in dev since neither directory exists there.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.maintenance.MaintenanceWriteFreezeMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# --- Database --------------------------------------------------------------

# Selected by MITO_DB_ENGINE ("postgres" | "sqlite"). Defaults to sqlite so a
# checkout with no database configuration keeps working exactly as before —
# important because deployments run from their own checkouts and their own .env.
#
# PostgreSQL is the primary development and concurrency-testing database:
# SQLite has no row-level locking (`select_for_update()` is a documented no-op
# there), which makes the assignment-concurrency tests meaningless. See
# docs/webknossos-transformation/benchmarks/BASELINE.md §5.
#
# Credentials come from the environment only — never hard-coded, never defaulted
# to a production value.
_db_engine = os.getenv("MITO_DB_ENGINE", "sqlite").strip().lower()

if _db_engine in ("postgres", "postgresql"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("MITO_DB_NAME", "mito_dev"),
            "USER": os.getenv("MITO_DB_USER", "mito"),
            "PASSWORD": os.getenv("MITO_DB_PASSWORD", ""),
            "HOST": os.getenv("MITO_DB_HOST", "127.0.0.1"),
            "PORT": os.getenv("MITO_DB_PORT", "5433"),
            # Persistent connections are a win for a dev server and a hazard for
            # tests: the concurrency suite spawns real threads, and a pooled
            # connection outliving its thread strands locks and makes a later
            # run hang on a row nobody is still using. Default to 0 under test
            # so that correctness never depends on remembering an env var;
            # MITO_DB_CONN_MAX_AGE still overrides either way.
            "CONN_MAX_AGE": int(
                os.getenv("MITO_DB_CONN_MAX_AGE", "0" if RUNNING_TESTS else "60")
            ),
            "OPTIONS": {
                # Fail loudly instead of hanging forever when a row lock is
                # genuinely stuck. Only applied under test: a real deployment
                # may legitimately run a long migration or report query.
                **(
                    {"options": "-c lock_timeout=10s -c statement_timeout=60s"}
                    if RUNNING_TESTS
                    else {}
                ),
            },
        }
    }
elif _db_engine == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            # Overridable so a throwaway copy can be migrated/inspected without
            # touching the real dev database.
            "NAME": os.getenv("MITO_SQLITE_NAME") or BASE_DIR / "db.sqlite3",
        }
    }
else:
    raise ImproperlyConfigured(
        f"MITO_DB_ENGINE must be 'postgres' or 'sqlite', got {_db_engine!r}."
    )


# --- Production hardening --------------------------------------------------
# Every one of these defaults to the *development* behaviour, so a checkout
# with no configuration and the whole test suite are unaffected. A deployment
# turns them on through the environment.
#
# Behind a TLS-terminating proxy (nginx, a Cloudflare tunnel) Django sees plain
# HTTP, so SECURE_SSL_REDIRECT alone would loop forever. MITO_PROXY_SSL_HEADER
# tells Django to trust the proxy's X-Forwarded-Proto instead; only enable it
# when a proxy really does set that header, or a client could spoof it.
if _env_bool("MITO_TRUST_PROXY_SSL_HEADER", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SECURE_SSL_REDIRECT = _env_bool("MITO_SECURE_SSL_REDIRECT", False)
SESSION_COOKIE_SECURE = _env_bool("MITO_SESSION_COOKIE_SECURE", False)
CSRF_COOKIE_SECURE = _env_bool("MITO_CSRF_COOKIE_SECURE", False)
SECURE_HSTS_SECONDS = int(os.getenv("MITO_SECURE_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool(
    "MITO_SECURE_HSTS_INCLUDE_SUBDOMAINS", False
)
SECURE_HSTS_PRELOAD = _env_bool("MITO_SECURE_HSTS_PRELOAD", False)
# The Cloudflare tunnel preserves the public Host header, so forwarded-host
# trust is unnecessary. Keep this explicit and opt-in for other topologies.
USE_X_FORWARDED_HOST = _env_bool("MITO_USE_X_FORWARDED_HOST", False)
SECURE_CONTENT_TYPE_NOSNIFF = _env_bool("MITO_SECURE_NOSNIFF", True)

# Django 4+ requires the scheme on every trusted origin.
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if o.strip()
]

# Upload / request ceilings. Volume registration posts real files, so the
# default 2.5 MB in-memory threshold is raised rather than the total limit.
DATA_UPLOAD_MAX_MEMORY_SIZE = int(
    os.getenv("MITO_MAX_UPLOAD_BYTES", str(64 * 1024 * 1024))
)
FILE_UPLOAD_MAX_MEMORY_SIZE = DATA_UPLOAD_MAX_MEMORY_SIZE
DATA_UPLOAD_MAX_NUMBER_FIELDS = int(
    os.getenv("MITO_MAX_FORM_FIELDS", "10000")
)


# --- Password validation ---------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# --- Internationalization --------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# --- Static & media files --------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Tests get a throwaway MITO_DATA_ROOT so a module that forgets to override it
# cannot deposit stub uploads into the real data tree — see config/test_runner.py
# for the incident this prevents. No effect outside `manage.py test`.
TEST_RUNNER = "config.test_runner.IsolatedDataRootRunner"

# Production: `npm run build --prefix frontend` (see config/urls.py's SPA
# catch-all). Vite's default absolute asset paths (/assets/...) need to be
# served from the URL root, which is what WHITENOISE_ROOT is for. Absent in
# dev (no frontend/dist there), so this is a no-op on a normal dev checkout.
FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
    WHITENOISE_ROOT = FRONTEND_DIST
    # Hashed Vite filenames already cache-bust; keep age low so CDN/browsers
    # pick up new deploys quickly during active iteration.
    WHITENOISE_MAX_AGE = 0

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# --- Authentication --------------------------------------------------------

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"


# --- Mito Data Agent settings ----------------------------------------------

# Root directory on the HPC / server / lab machine where image volumes,
# optional labels, submissions, and generated task files live. The database
# stores paths relative to this root, not the large image data itself.
#
# A relative MITO_DATA_ROOT is resolved against the repository root (the parent
# of ``backend/``), so values like ``./data`` mean the same thing regardless of
# the process's current working directory. The default lives inside the repo.
_mito_data_root_env = os.getenv("MITO_DATA_ROOT")
if _mito_data_root_env:
    _mito_data_root = Path(_mito_data_root_env)
    if not _mito_data_root.is_absolute():
        _mito_data_root = BASE_DIR.parent / _mito_data_root
else:
    _mito_data_root = BASE_DIR / "mito_data_root"
MITO_DATA_ROOT = _mito_data_root.resolve()

# Allowed file extensions for uploaded/registered label files (basic QC).
MITO_ALLOWED_LABEL_EXTENSIONS = [
    ".tif",
    ".tiff",
    ".h5",
    ".hdf5",
    ".zarr",
    ".npy",
    ".nii",
    ".nii.gz",
]

# Which dataset inside a registered .h5/.hdf5 file holds the volume. Empty
# means "work it out": the conventional names first (``main``, ``data``,
# ``raw``, …), then the file's only 3-D dataset. Set this only for files that
# contain several candidate volumes, where guessing would silently annotate
# the wrong one. See annotation/visualization/hdf5_io.py.
MITO_HDF5_DATASET = os.getenv("MITO_HDF5_DATASET", "").strip()


# --- Modular provider selection --------------------------------------------
# Each replaceable integration is chosen by name here; the domain services call
# the provider registry, never a low-level adapter directly. See progress/codemap.md
# for the folder that owns each provider.
MITO_QC_PROVIDER = os.getenv("MITO_QC_PROVIDER", "basic")
MITO_VISUALIZATION_PROVIDER = os.getenv("MITO_VISUALIZATION_PROVIDER", "inapp")
MITO_PUBLISHING_PROVIDER = os.getenv("MITO_PUBLISHING_PROVIDER", "placeholder")

# SAM2 fork-aware tracking. Defaults point at vendor/sam2/ (hiera_large via
# Git LFS). Needs the pytorch stack from environment.yml. Use
# MITO_TRACKING_PROVIDER=local for CPU-only CI. Override paths via .env if
# weights live elsewhere.
MITO_TRACKING_PROVIDER = os.getenv("MITO_TRACKING_PROVIDER", "sam2")
_default_sam2_root = BASE_DIR.parent / "vendor" / "sam2"
MITO_SAM2_ROOT = os.getenv("MITO_SAM2_ROOT", str(_default_sam2_root))
# Default to hiera_large (best accuracy). Only this checkpoint is kept under
# vendor/sam2/checkpoints/.
MITO_SAM2_CHECKPOINT = os.getenv(
    "MITO_SAM2_CHECKPOINT",
    str(_default_sam2_root / "checkpoints" / "sam2.1_hiera_large.pt"),
)
MITO_SAM2_CONFIG = os.getenv("MITO_SAM2_CONFIG", "configs/sam2.1/sam2.1_hiera_l.yaml")
MITO_SAM2_CUDA_DEVICE = int(os.getenv("MITO_SAM2_CUDA_DEVICE", "0"))

# Cellable-ported interactive AI-mask tools (Point Mask / Box Mask / Boundary
# — see backend/annotation/cellable_port/). Weights under vendor/efficient_sam/
# (vits ONNX). onnxruntime/skimage/scipy come from environment.yml —
# see cellable_port/ai/registry.py for graceful "unavailable" if missing.
_default_efficient_sam_root = BASE_DIR.parent / "vendor" / "efficient_sam"
MITO_CELLABLE_MODELS_ROOT = os.getenv(
    "MITO_CELLABLE_MODELS_ROOT", str(_default_efficient_sam_root)
)
MITO_EFFICIENT_SAM_VARIANT = os.getenv("MITO_EFFICIENT_SAM_VARIANT", "vits")

MITO_AI_ROI_TARGET_SIZE = int(os.getenv("MITO_AI_ROI_TARGET_SIZE", "1024"))
MITO_AI_ROI_MAX_SIZE = int(os.getenv("MITO_AI_ROI_MAX_SIZE", "1536"))
MITO_AI_ROI_POINT_PAD = int(os.getenv("MITO_AI_ROI_POINT_PAD", "256"))
MITO_AI_ROI_BOX_PAD = int(os.getenv("MITO_AI_ROI_BOX_PAD", "64"))
MITO_AI_ROI_SNAP = int(os.getenv("MITO_AI_ROI_SNAP", "64"))

# Optional acceleration: session creation always falls back to CPU, but logs
# the effective providers so a production CUDA misconfiguration is visible.
MITO_AI_ONNX_CUDA = _env_bool("MITO_AI_ONNX_CUDA", True)
MITO_AI_CUDA_DEVICE = os.getenv("MITO_AI_CUDA_DEVICE") or None

MITO_SAM2_XY_PAD = int(os.getenv("MITO_SAM2_XY_PAD", "256"))
MITO_SAM2_XY_MAX = int(os.getenv("MITO_SAM2_XY_MAX", "2048"))
MITO_SAM2_XY_MIN = int(os.getenv("MITO_SAM2_XY_MIN", "512"))

# When on, the EfficientSAM path logs per-request timing (embed source —
# in-process / disk / encoder — plus decode ms) via the "mito.ai.timing"
# logger at INFO, so a latency regression (e.g. a cold encoder running on
# every click because the disk cache stopped hitting) is obvious in the
# server log. Off by default; turn on with MITO_AI_TIMING=1.
MITO_AI_TIMING = _env_bool("MITO_AI_TIMING", False)


# --- Logging ---------------------------------------------------------------
# Without an explicit LOGGING dict, Django's default config only wires up the
# ``django`` logger; everything else falls through to logging's *last resort*
# handler, which is pinned at WARNING. That silently discarded every INFO line
# this app emits — including the two the flag above and the SAM adapters exist
# to produce:
#
#   * ``mito.ai.timing`` / ``mito.track.timing`` — the per-request EfficientSAM
#     and SAM2 timings ``MITO_AI_TIMING=1`` is supposed to turn on;
#   * ``annotation.cellable_port.ai.efficient_sam`` — the one line that says
#     which ONNX execution provider actually attached, i.e. whether the AI path
#     is on CUDA or has quietly fallen back to CPU.
#
# Both matter operationally and neither reached the log. Handlers write to
# stderr, which the gunicorn unit captures into ``logs/error.log`` via
# ``--capture-output``; ``MITO_LOG_LEVEL`` dials the app loggers down (e.g. to
# WARNING) without a code change.
MITO_LOG_LEVEL = os.getenv("MITO_LOG_LEVEL", "INFO").upper()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "mito": {
            "format": "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "stderr": {
            "class": "logging.StreamHandler",
            "stream": sys.stderr,
            "formatter": "mito",
        },
    },
    "root": {"handlers": ["stderr"], "level": "WARNING"},
    "loggers": {
        # Timing channels. Emitted only when MITO_AI_TIMING is on, so this is
        # free when the flag is off.
        "mito": {"level": MITO_LOG_LEVEL, "propagate": True},
        # Runtime/device selection and provider load for the two model stacks.
        "annotation.cellable_port.ai": {"level": MITO_LOG_LEVEL, "propagate": True},
        "annotation.tracking": {"level": MITO_LOG_LEVEL, "propagate": True},
        # Django's own request logger already defaults to ERROR-only for 5xx;
        # keep that, but let WARNING (4xx) through so bad requests are visible.
        "django.request": {"level": "WARNING", "propagate": True},
    },
}

# --- Feature flags ---------------------------------------------------------
# Teams and access audit. With
# this off, every permission check behaves exactly as it did before, so the
# schema can be migrated and backfilled long before any behaviour switches
# over. Turn on only once projects have teams granted.
FEATURE_TEAMS = _upgrade_feature("FEATURE_TEAMS")

# Push auto-fill remains a manager planning aid. It assigns the canonical task
# row directly; the retired pull-claim hierarchy and lease engine are gone.
FEATURE_AUTO_FILL_SCHEDULER = _upgrade_feature("FEATURE_AUTO_FILL_SCHEDULER")

# Hard ceiling on assignments considered in one scheduler tick. This is what
# keeps the batch's lock short and its scan bounded; a tick that cannot place
# all available work places the rest on the next tick. Raising it trades
# scheduling latency for longer-held row locks.
MITO_SCHEDULER_MAX_BATCH = int(os.getenv("MITO_SCHEDULER_MAX_BATCH", "200"))

# An annotator must have been seen within this window to be considered
# available for manager auto-fill. 0 disables the recency gate.
MITO_SCHEDULER_ACTIVE_DAYS = int(os.getenv("MITO_SCHEDULER_ACTIVE_DAYS", "14"))

# Deterministic scoring weights (see ADR-002 and research doc 18). Overridable
# so fairness can be tuned without a code change; every weight is applied to a
# normalised 0..1 component so they stay comparable.
# Phase 5: append-only submission history, immutable reviews, and enforced task
# status transitions. It works directly on the canonical single-assignee task.
#
# Off: resubmitting deletes the previous submission (today's behaviour) and an
# illegal status transition is logged but permitted.
# On: prior submissions are retained and marked superseded, and an illegal
# transition raises. Retaining uploaded files means submission storage grows
# with review rounds — see ADR-003 §2, conflict A.
FEATURE_REVIEW_HISTORY = _upgrade_feature("FEATURE_REVIEW_HISTORY")

# Phase 6: dashboard and statistics endpoints. Gates only the new read-only
# endpoints — the aggregate query fix inside calculate_project_progress is NOT
# gated, because it changes how a number is computed rather than what it is.
# Requires no other flag: with everything else off the project dashboard still
# reports task statuses and elapsed durations, and the instance chart is
# legitimately empty because no instances exist.
FEATURE_DASHBOARDS = _upgrade_feature("FEATURE_DASHBOARDS")

# Phase 7: append-only annotation operation log and work sessions. Named
# FEATURE_ANNOTATION_OPS because the master prompt names it that; an
# authoritative name beats a more descriptive one.
#
# Off: nothing is recorded and every read/write path is byte-identical to
# Phase 6. On: edits append operations and editing sessions accrue measured
# active time. The materialized working label stays authoritative for reads
# either way — the log is history and undo substrate, not the source of truth.
FEATURE_ANNOTATION_OPS = _upgrade_feature("FEATURE_ANNOTATION_OPS")

# Phase 8: SDF-blend interpolation between two labelled slices. Requires
# FEATURE_ANNOTATION_OPS because applying an interpolation records exactly one
# Phase 7 operation — the "single undoable transaction" the spec asks for.
# Planning (the preview half) needs only this flag.
FEATURE_INTERPOLATION = _upgrade_feature("FEATURE_INTERPOLATION")

# Phase 9: the ranked P1 annotation tools (flood fill, overwrite policies, deep
# links). Planning needs only this flag; APPLYING also needs
# FEATURE_ANNOTATION_OPS, exactly as interpolation does, because an apply
# records one Phase 7 operation.
FEATURE_ANNOTATION_TOOLS = _upgrade_feature("FEATURE_ANNOTATION_TOOLS")

# Phase 11: Zarr v3 interactive derivatives (ADR-009). Off by default; the
# derivative is additive, so with this off nothing is written and nothing about
# existing reads changes. Building also needs the optional `zarr` dependency,
# which is imported lazily so an environment without it still starts.
FEATURE_VOLUME_PYRAMIDS = _upgrade_feature("FEATURE_VOLUME_PYRAMIDS")

# Phase 12: the chunk/datastore service (ADR-010). Off by default. Serving also
# requires FEATURE_VOLUME_PYRAMIDS — without a derivative there is nothing to
# serve — so endpoints exist but report themselves disabled rather than
# appearing merely because pyramid files are on disk.
FEATURE_CHUNK_SERVICE = _upgrade_feature("FEATURE_CHUNK_SERVICE")

# Signed chunk tokens. The signing key is derived from SECRET_KEY with a
# distinct salt, so no signing secret is stored in the database. Rotating the
# key version invalidates every outstanding token at once, which is the only
# immediate revocation this design offers — see ADR-010 §4.
MITO_CHUNK_TOKEN_TTL_SECONDS = int(
    os.getenv("MITO_CHUNK_TOKEN_TTL_SECONDS", "300")
)
MITO_CHUNK_TOKEN_KEY_VERSION = os.getenv("MITO_CHUNK_TOKEN_KEY_VERSION", "1")

# Per-request ceilings for the chunk path.
MITO_CHUNK_MAX_VOXELS = int(os.getenv("MITO_CHUNK_MAX_VOXELS", str(4 * 1024 * 1024)))
MITO_CHUNK_MAX_BYTES = int(os.getenv("MITO_CHUNK_MAX_BYTES", str(32 * 1024 * 1024)))

# Phase 19 operational endpoints. Metrics stay disabled (404) until a bearer
# token is configured; liveness/readiness never expose paths or credentials.
MITO_METRICS_BEARER_TOKEN = os.getenv("MITO_METRICS_BEARER_TOKEN", "")
MITO_READY_MIN_FREE_BYTES = int(
    os.getenv("MITO_READY_MIN_FREE_BYTES", str(1024 * 1024 * 1024))
)

# Abuse limits. Checked before allocation so a small request cannot trigger a
# large scan.
MITO_TOOL_MAX_VOXELS = int(
    os.getenv("MITO_TOOL_MAX_VOXELS", str(16 * 1024 * 1024))
)
# "Limited 3D" from doc 19: a depth ceiling on top of the voxel cap, so a 3-D
# fill cannot quietly become a whole-volume flood.
MITO_TOOL_MAX_FILL_DEPTH = int(os.getenv("MITO_TOOL_MAX_FILL_DEPTH", "32"))

# Hard ceiling on an interpolation request, counted as plane voxels x depth.
# Guards the float64 signed-distance intermediates, which cost 8 bytes per
# voxel each. Over this the call is refused before any allocation rather than
# after an OOM.
MITO_INTERPOLATION_MAX_VOXELS = int(
    os.getenv("MITO_INTERPOLATION_MAX_VOXELS", str(64 * 1024 * 1024))
)

# Operation payloads are metadata, not voxels. Anything larger must be stored
# by reference (payload_ref) so the log stays scannable without touching image
# data. 16 KiB.
MITO_OP_PAYLOAD_MAX_BYTES = int(os.getenv("MITO_OP_PAYLOAD_MAX_BYTES", str(16 * 1024)))

# --- Active-time accounting (Phase 7) --------------------------------------
# Every credited second comes from the server clock; client timestamps are
# stored for diagnostics and never trusted for duration.
#
# A heartbeat credits at most this much, so a sleeping tab that wakes after an
# hour credits two minutes rather than an hour.
MITO_SESSION_MAX_HEARTBEAT_SECONDS = int(
    os.getenv("MITO_SESSION_MAX_HEARTBEAT_SECONDS", "120")
)
# A gap longer than this credits nothing at all and begins a new active span.
MITO_SESSION_IDLE_TIMEOUT_SECONDS = int(
    os.getenv("MITO_SESSION_IDLE_TIMEOUT_SECONDS", "300")
)

MITO_SCHEDULER_WEIGHTS = {
    "project_priority": float(os.getenv("MITO_SCHED_W_PROJECT_PRIORITY", "3.0")),
    "task_priority": float(os.getenv("MITO_SCHED_W_TASK_PRIORITY", "2.0")),
    "deadline_urgency": float(os.getenv("MITO_SCHED_W_DEADLINE", "2.0")),
    "quality_history": float(os.getenv("MITO_SCHED_W_QUALITY", "1.0")),
    "current_load": float(os.getenv("MITO_SCHED_W_LOAD", "2.0")),      # subtracted
    "fairness_bonus": float(os.getenv("MITO_SCHED_W_FAIRNESS", "1.5")),
}

# Processing/HPC backend for ProcessingJob execution ("local" or "slurm").
MITO_PROCESSING_BACKEND = os.getenv("MITO_PROCESSING_BACKEND", "local")

# Real local ProcessingJob execution is opt-in. `config.argv` never goes
# through a shell and argv[0] must match this basename allow-list.
MITO_LOCAL_EXECUTABLE_ALLOWLIST = os.getenv(
    "MITO_LOCAL_EXECUTABLE_ALLOWLIST", ""
)
MITO_LOCAL_JOB_TIMEOUT_SECONDS = int(
    os.getenv("MITO_LOCAL_JOB_TIMEOUT_SECONDS", "86400")
)
MITO_PROCESSING_ENV_ALLOWLIST = {
    value.strip()
    for value in os.getenv(
        "MITO_PROCESSING_ENV_ALLOWLIST",
        "nnUNet_raw,nnUNet_preprocessed,nnUNet_results,CUDA_VISIBLE_DEVICES,OMP_NUM_THREADS",
    ).split(",")
    if value.strip()
}

# Shared storage root for processing inputs/outputs/logs. Defaults to the data
# root so local development works without extra configuration.
MITO_SHARED_STORAGE_ROOT = os.getenv("MITO_SHARED_STORAGE_ROOT", str(MITO_DATA_ROOT))

# Optional external visualization URL. Left blank means "not configured".
MITO_NEUROGLANCER_BASE_URL = os.getenv("MITO_NEUROGLANCER_BASE_URL", "")

# --- SLURM adapter configuration (all lab-specific values come from env) ----
MITO_SLURM_PARTITION = os.getenv("MITO_SLURM_PARTITION", "")
MITO_SLURM_ACCOUNT = os.getenv("MITO_SLURM_ACCOUNT", "")
MITO_SLURM_SBATCH = os.getenv("MITO_SLURM_SBATCH", "sbatch")
MITO_SLURM_SQUEUE = os.getenv("MITO_SLURM_SQUEUE", "squeue")
MITO_SLURM_SACCT = os.getenv("MITO_SLURM_SACCT", "sacct")
MITO_SLURM_SCANCEL = os.getenv("MITO_SLURM_SCANCEL", "scancel")


# --- Django REST Framework -------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": None,
}


# --- CORS (React dev server) ----------------------------------------------

CORS_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "DJANGO_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if o.strip()
]
CORS_ALLOW_CREDENTIALS = True
