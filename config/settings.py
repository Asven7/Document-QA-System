"""Django settings for the Document QA System project.

Every deployment-specific value is read from environment variables so that
the exact same code runs in local development and inside Docker Compose.
Safe defaults are provided for everything that is not a secret; see
``.env.example`` for a documented template of all supported variables.
"""

import os
import warnings
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# Load the project .env file (development convenience). Real environment
# variables always win over .env values, and inside Docker Compose the same
# file is injected by ``env_file`` - so both setups behave identically.
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:  # pragma: no cover - python-dotenv is in requirements.txt
    pass


# ---------------------------------------------------------------------------
# Small helpers for reading environment variables
# ---------------------------------------------------------------------------
def env_str(name: str, default: str = "") -> str:
    """Return an environment variable as a stripped string."""
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip()


def env_bool(name: str, default: bool = False) -> bool:
    """Return an environment variable as a boolean."""
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    """Return an environment variable as an integer, falling back to *default*."""
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        raise ImproperlyConfigured(
            f"Environment variable {name} must be an integer, got {value!r}."
        ) from None


def env_float(name: str, default: float) -> float:
    """Return an environment variable as a float, falling back to *default*."""
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        raise ImproperlyConfigured(
            f"Environment variable {name} must be a number, got {value!r}."
        ) from None


def env_list(name: str, default: str = "") -> list[str]:
    """Return a comma-separated environment variable as a list of strings."""
    return [item.strip() for item in env_str(name, default).split(",") if item.strip()]


def resolve_path(value: str) -> Path:
    """Resolve *value* relative to the project root when it is not absolute."""
    path = Path(value)
    return path if path.is_absolute() else BASE_DIR / path


# ---------------------------------------------------------------------------
# Core Django configuration
# ---------------------------------------------------------------------------
SECRET_KEY = env_str("DJANGO_SECRET_KEY", "insecure-local-development-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

if not DEBUG and SECRET_KEY == "insecure-local-development-key-change-me":
    warnings.warn(
        "DJANGO_SECRET_KEY is left at its insecure development default. "
        "Generate a strong secret key before deploying (see README, section 9).",
        stacklevel=1,
    )

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party apps
    "rest_framework",
    "rest_framework.authtoken",
    "drf_spectacular",
    # Local apps
    "core",
    "documents",
    "questions",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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

# Test runner that redirects MEDIA_ROOT to a temporary directory so that
# upload tests never leave files in the developer's real media folder.
TEST_RUNNER = "core.test_runner.IsolatedMediaRunner"

# ---------------------------------------------------------------------------
# Database
#
# PostgreSQL is the database used by Docker Compose (the ``db`` service).
# ``DJANGO_DB=sqlite`` is provided as a convenience for quick local runs and
# for the automated test suite, where Django uses an in-memory SQLite database.
# ---------------------------------------------------------------------------
_database_engine = env_str("DJANGO_DB", "postgres")
if _database_engine not in {"postgres", "sqlite"}:
    raise ImproperlyConfigured(
        f"DJANGO_DB must be either 'postgres' or 'sqlite', got {_database_engine!r}."
    )

if _database_engine == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": resolve_path(env_str("SQLITE_PATH", "db.sqlite3")),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env_str("POSTGRES_DB", "documentqa"),
            "USER": env_str("POSTGRES_USER", "documentqa"),
            "PASSWORD": env_str("POSTGRES_PASSWORD", "documentqa"),
            "HOST": env_str("POSTGRES_HOST", "localhost"),
            "PORT": env_str("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = resolve_path(env_str("STATIC_ROOT", "staticfiles"))
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

MEDIA_ROOT = resolve_path(env_str("MEDIA_ROOT", "media"))
MEDIA_URL = "media/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        # Token authentication is the recommended mechanism for API clients.
        # It is listed first so that unauthenticated API calls receive a
        # proper 401 with a ``WWW-Authenticate: Token`` challenge (DRF only
        # sends 401 when the first authenticator exposes a challenge header;
        # with SessionAuthentication first it would coerce 401 to 403).
        "rest_framework.authentication.TokenAuthentication",
        # Session authentication powers the browsable API and Django Admin.
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "core.pagination.DefaultPagination",
    "PAGE_SIZE": env_int("API_PAGE_SIZE", 20),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Document QA System API",
    "DESCRIPTION": (
        "REST API for the LLM-based document question answering system. "
        "Upload DOCX documents, ask questions about their content and "
        "retrieve grounded, source-cited answers generated with LangChain "
        "and an OpenRouter-hosted LLM. Persian (Farsi) and English are fully "
        "supported in all text fields."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SWAGGER_UI_SETTINGS": {
        "persistAuth": True,
    },
}

# ---------------------------------------------------------------------------
# Document processing configuration
# ---------------------------------------------------------------------------
UPLOAD_MAX_FILE_SIZE_MB = env_int("UPLOAD_MAX_FILE_SIZE_MB", 10)
CHUNK_SIZE = env_int("CHUNK_SIZE", 800)
CHUNK_OVERLAP = env_int("CHUNK_OVERLAP", 150)
# Hard cap for manually entered/pasted document text (characters).
DOCUMENT_MAX_TEXT_CHARS = env_int("DOCUMENT_MAX_TEXT_CHARS", 500000)
# When True (production default), document processing (chunking + embedding
# + indexing) runs in a background worker thread so requests never wait for
# embedding - which can include a one-time model download on first start.
# The test suite forces this to False for deterministic assertions.
DOCUMENT_PROCESSING_ASYNC = env_bool("DOCUMENT_PROCESSING_ASYNC", True)

if CHUNK_SIZE <= 0 or CHUNK_OVERLAP < 0 or CHUNK_OVERLAP >= CHUNK_SIZE:
    raise ImproperlyConfigured(
        f"Invalid chunking configuration: CHUNK_SIZE={CHUNK_SIZE}, "
        f"CHUNK_OVERLAP={CHUNK_OVERLAP}. Require CHUNK_SIZE > 0 and "
        "0 <= CHUNK_OVERLAP < CHUNK_SIZE."
    )

# ---------------------------------------------------------------------------
# Embeddings and vector store configuration
# ---------------------------------------------------------------------------
EMBEDDINGS_PROVIDER = env_str("EMBEDDINGS_PROVIDER", "fastembed")
if EMBEDDINGS_PROVIDER not in {"fastembed", "fake"}:
    raise ImproperlyConfigured(
        f"EMBEDDINGS_PROVIDER must be 'fastembed' or 'fake', got {EMBEDDINGS_PROVIDER!r}. "
        "'fake' provides deterministic offline embeddings and is intended for tests."
    )

# Multilingual embedding model with strong Persian support (see
# documents/services/embeddings.py for the full list of supported models).
EMBEDDING_MODEL = env_str("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B-Q")

# Where fastembed caches the ONNX model files after the first download
# (~1.1 GB for the default model). Docker Compose mounts a persistent volume
# at ``model_cache`` so the download happens exactly once; locally it is a
# folder next to ``manage.py``.
FASTEMBED_CACHE_PATH = resolve_path(env_str("FASTEMBED_CACHE_PATH", "model_cache"))

CHROMA_PERSIST_DIRECTORY = resolve_path(
    env_str("CHROMA_PERSIST_DIRECTORY", "chroma_data")
)

# ---------------------------------------------------------------------------
# Retrieval configuration
# ---------------------------------------------------------------------------
RETRIEVAL_STRATEGY = env_str("RETRIEVAL_STRATEGY", "hybrid")
if RETRIEVAL_STRATEGY not in {"hybrid", "semantic"}:
    raise ImproperlyConfigured(
        f"RETRIEVAL_STRATEGY must be 'hybrid' or 'semantic', got {RETRIEVAL_STRATEGY!r}."
    )

RETRIEVAL_TOP_K = env_int("RETRIEVAL_TOP_K", 6)
RETRIEVAL_CANDIDATE_MULTIPLIER = env_int("RETRIEVAL_CANDIDATE_MULTIPLIER", 3)
RETRIEVAL_RRF_K = env_int("RETRIEVAL_RRF_K", 60)
RETRIEVAL_MIN_SCORE = env_float("RETRIEVAL_MIN_SCORE", 0.0)
RETRIEVAL_USE_MMR = env_bool("RETRIEVAL_USE_MMR", False)

if RETRIEVAL_TOP_K <= 0 or RETRIEVAL_CANDIDATE_MULTIPLIER < 1:
    raise ImproperlyConfigured(
        "RETRIEVAL_TOP_K must be positive and RETRIEVAL_CANDIDATE_MULTIPLIER >= 1."
    )

# ---------------------------------------------------------------------------
# LLM (OpenRouter) configuration
#
# The application starts and serves the Admin, health checks and document
# management even without an API key; only question answering requires a
# configured LLM, in which case the API reports a clear error.
# ---------------------------------------------------------------------------
OPENROUTER_API_KEY = env_str("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = env_str("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = env_str(
    "OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"
)
LLM_TEMPERATURE = env_float("LLM_TEMPERATURE", 0.1)
LLM_MAX_TOKENS = env_int("LLM_MAX_TOKENS", 1024)
LLM_TIMEOUT_SECONDS = env_int("LLM_TIMEOUT_SECONDS", 60)
LLM_MAX_RETRIES = env_int("LLM_MAX_RETRIES", 2)

# ---------------------------------------------------------------------------
# Logging
#
# Log messages use English. Document content and question text are treated as
# untrusted data and are never logged; only identifiers and counters appear in
# log lines.
# ---------------------------------------------------------------------------
LOG_LEVEL = env_str("DJANGO_LOG_LEVEL", "INFO").upper()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "django": {"level": LOG_LEVEL},
        "documents": {"level": LOG_LEVEL},
        "questions": {"level": LOG_LEVEL},
        "core": {"level": LOG_LEVEL},
    },
}
