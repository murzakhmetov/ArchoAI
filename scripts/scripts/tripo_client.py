"""Client for the Tripo 3D API v3 (https://developers.tripo3d.com).

Only what ArchoAI needs is implemented: upload a photo, create an image-to-model or
multiview-to-model task, poll the task, and download the finished GLB.

Design notes
------------
* Imports only the standard library and ``requests``, so the module can be used and unit-tested
  without torch / OpenCV / dotenv.  Environment variables are read in ``TripoClient.from_env()``.
* One base URL per client.  Tripo accounts and API keys belong to a region (global
  openapi.tripo3d.ai or China openapi.tripo3d.com): a key sent to the wrong region is rejected, and
  a task id exists only in the region that created it.  There is therefore no automatic fallback
  from one host to the other.  Choose the region with TRIPO_REGION=global|china|cn, or set
  TRIPO_BASE_URL.
* Creating a task costs credits and is not idempotent.  ``create_*`` calls are repeated only after
  HTTP 429 (the request was refused, nothing was created).  A read timeout, a reset connection, a
  broken body or a 5xx answer is reported to the caller and never retried blindly (a retry could
  bill twice).  Uploads, task queries and downloads are safe: network errors, 429, 5xx and a 200
  answer that is not an API envelope (proxy page, empty body) are retried for them.
* API calls never follow redirects: ``requests`` would send a paid POST again after a 307/308, with
  the Authorization header.  Any 3xx is a ``TripoError`` and means the request was not processed.
  Only the download of the signed GLB URL follows redirects, and it carries no credentials.
* Retries are bounded: at most 3 attempts per call and 60 s of back-off in total.  The wait before
  a retry is the larger of the back-off step (1, 2, 4 ... s) and Retry-After.  A server that asks
  for a longer wait gets a ``TripoError``, not a long sleep.  One download attempt is also capped
  at 10 minutes of wall-clock time (checked after every 8 KB chunk), because ``download_timeout``
  only bounds each single read.
* ``wait_for_task`` rides out an outage of the status endpoint for up to 3 minutes since the last
  successful poll, and raises at once for permanent errors (401/403/404, other 4xx).
* The client raises ``TripoError`` and nothing else for runtime failures.
* The API key goes only into the Authorization header of API calls.  It is never sent to the
  signed download URL and never appears in error messages.
"""

from __future__ import annotations

import logging
import math
import os
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import quote, urlparse

import requests

__all__ = [
    "CHINA_BASE_URL",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "VIEW_KEYS",
    "TripoClient",
    "TripoError",
    "TripoInputError",
    "TripoTask",
]

logger = logging.getLogger(__name__)

# Global endpoint (the default) and the China endpoint.  Keys are bound to a region, so the client
# never switches between them on its own; see TRIPO_REGION / TRIPO_BASE_URL in TripoClient.from_env().
DEFAULT_BASE_URL = "https://openapi.tripo3d.ai/v3"
CHINA_BASE_URL = "https://openapi.tripo3d.com/v3"
DEFAULT_MODEL = "v3.1-20260211"
_REGION_URLS: Dict[str, str] = {"global": DEFAULT_BASE_URL, "china": CHINA_BASE_URL, "cn": CHINA_BASE_URL}

# multiview-to-model views, in the order photos are mapped to them.
VIEW_KEYS: Tuple[str, ...] = ("front", "left", "back", "right")

# Keys of task["output"] that may hold the GLB link, best first.  "model_url" is the v3 name, the
# rest are legacy v2 names.  Only the output object is searched: in v3 the word "model" is also
# the request field that carries the version string, so the whole response must never be scanned.
_MODEL_URL_KEYS: Tuple[str, ...] = ("model_url", "pbr_model", "model", "base_model")

_FAILED_STATUSES = frozenset({"failed", "cancelled", "banned", "expired"})
_GLB_MAGIC = b"glTF"
_GLB_HEADER_BYTES = 12                    # magic, version and total length (uint32 little-endian)

_MAX_UPLOAD_BYTES = 20 * 1024 * 1024      # Tripo accepts images up to 20 MB
_MAX_DOWNLOAD_BYTES = 300 * 1024 * 1024   # sanity limit for the GLB held in memory
_MAX_DOWNLOAD_SECONDS = 600.0             # wall-clock cap of one download attempt (reads are bounded one by one)
_DOWNLOAD_CHUNK_BYTES = 8192              # small on purpose: the cap is checked once per chunk, and a slow
#                                           trickle needs a long time to fill a big chunk
_CONNECT_TIMEOUT = 10.0                   # a dead host must not hold up a call for minutes

_MAX_ATTEMPTS = 3                         # per call, the first try included
_MAX_TOTAL_WAIT = 60.0                    # seconds of back-off one call may spend
_BACKOFF_STEPS = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0)
_MAX_OUTAGE = 180.0                       # wait_for_task gives up after this long without a good poll
_MAX_POLLS_WITHOUT_URL = 3                # extra polls after "success" that has no model URL
_MAX_POLL_PAUSE = 30.0
_MIN_POLL_INTERVAL = 0.05                 # keeps a poll_interval of 0 from becoming a busy loop

_HINT_AUTH = "Проверьте TRIPO_API_KEY в .env: ключ не указан или недействителен."
_HINT_LIMIT = "Достигнут лимит запросов или параллельных задач Tripo. Подождите немного и повторите."
_HINT_SERVER = "Сервер Tripo временно недоступен или вернул ошибку. Повторите попытку позже."
_HINT_NETWORK = (
    "Нет связи с Tripo API: проверьте интернет, VPN/прокси, TRIPO_REGION и TRIPO_BASE_URL."
)
_HINT_URL = "Проверьте TRIPO_BASE_URL: адрес должен начинаться с https:// и заканчиваться на /v3."
_HINT_LOCAL = (
    "Запрос не удалось отправить из-за локальной ошибки: проверьте REQUESTS_CA_BUNDLE/SSL_CERT_FILE, "
    "настройки прокси и значение TRIPO_API_KEY."
)
_HINT_TASK_FAILED = (
    "Tripo не смог построить модель (кредиты за такую задачу возвращаются). "
    "Попробуйте переснять объект: чёткий кадр, ровный фон, без пересветов."
)
_HINT_TASK_PENDING = "Задача могла завершиться позже: найдите её в панели Tripo по Task ID."

# Request-level error codes (see the "Error Handling" page of the Tripo docs).
ERROR_HINTS: Dict[int, str] = {
    2: _HINT_AUTH,
    1002: _HINT_AUTH,
    1000: _HINT_SERVER,
    1001: _HINT_SERVER,
    1003: "Tripo не смог разобрать тело запроса (некорректный JSON). Это ошибка клиента.",
    1004: "Tripo отклонил параметры запроса: проверьте TRIPO_MODEL и TRIPO_FACE_LIMIT.",
    1007: _HINT_LIMIT,
    2000: _HINT_LIMIT,
    2001: "Задача не найдена в Tripo: неверный Task ID или она создана в другом регионе (TRIPO_REGION).",
    2002: "Tripo не поддерживает этот тип задачи.",
    2003: "Загруженный файл изображения пустой.",
    2004: "Tripo не поддерживает формат файла: нужен JPEG или PNG.",
    2008: "Tripo отклонил изображение из-за политики контента. Попробуйте другое фото.",
    2010: "На аккаунте Tripo недостаточно кредитов. Пополните баланс в личном кабинете Tripo.",
    2015: "Версия модели Tripo устарела. Укажите актуальную версию в TRIPO_MODEL.",
    2018: "Модель слишком сложная для обработки Tripo. Попробуйте другие снимки.",
    4001: "Эндпоинт Tripo не найден: проверьте TRIPO_BASE_URL.",
}
_HTTP_HINTS: Dict[int, str] = {
    401: _HINT_AUTH,
    403: "Доступ запрещён: проверьте права API-ключа, регион (TRIPO_REGION) и баланс кредитов Tripo.",
    404: "Ресурс Tripo не найден: проверьте TRIPO_BASE_URL и Task ID.",
    429: _HINT_LIMIT,
}


def _default_hint(code: Optional[int], http_status: Optional[int]) -> Optional[str]:
    if code in ERROR_HINTS:
        return ERROR_HINTS[code]
    if http_status in _HTTP_HINTS:
        return _HTTP_HINTS[http_status]
    if http_status is not None and http_status >= 500:
        return _HINT_SERVER
    return None


class TripoError(Exception):
    """Any failure of the Tripo integration: API error, network problem or unusable answer.

    Attributes:
        message: short technical description (English, taken from the API when there is one).
        code: request-level Tripo error code (``None`` when the failure did not come from an
            error envelope).
        http_status: HTTP status of the answer, if there was one.
        request_id / suggestion: copied from the error envelope.
        hint: short friendly text (Russian) with the likely fix, for known failures.
        transient: the same call may succeed later (network error, 429, 5xx).
        maybe_created: the request may have reached Tripo although no usable answer arrived.
            Callers must not repeat a paid ``create_*`` call when this is true.
        retry_after: seconds from the Retry-After header of a 429/5xx answer, if any.
        task_error_code: ``error_code`` of a failed task.  It is a different namespace than
            ``code``, which is why it is kept apart.
    """

    def __init__(
        self,
        message: str,
        code: Optional[int] = None,
        http_status: Optional[int] = None,
        request_id: Optional[str] = None,
        suggestion: Optional[str] = None,
        hint: Optional[str] = None,
        *,
        transient: bool = False,
        maybe_created: bool = False,
        retry_after: Optional[float] = None,
        task_error_code: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status
        self.request_id = request_id
        self.suggestion = suggestion
        self.hint = hint if hint is not None else _default_hint(code, http_status)
        self.transient = transient
        self.maybe_created = maybe_created
        self.retry_after = retry_after
        self.task_error_code = task_error_code

    def __str__(self) -> str:
        details = []
        if self.code is not None:
            details.append(f"code {self.code}")
        if self.http_status is not None:
            details.append(f"HTTP {self.http_status}")
        if self.request_id:
            details.append(f"request_id {self.request_id}")
        text = self.message
        if details:
            text += " (" + ", ".join(details) + ")"
        if self.suggestion:
            text += f". Suggestion: {self.suggestion}"
        return text


class TripoInputError(TripoError, ValueError):
    """The caller passed unusable arguments (nothing was sent to Tripo)."""


def _normalize_status(value: Any) -> str:
    """Lower-cased status text; anything that is not a non-blank string becomes "unknown"."""
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return "unknown"


@dataclass
class TripoTask:
    """State of a Tripo task as returned by ``GET /tasks/{task_id}``."""

    task_id: str
    status: str
    progress: int = 0
    model_url: Optional[str] = None
    rendered_image_url: Optional[str] = None
    error_code: Optional[int] = None
    error_message: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.status = _normalize_status(self.status)

    @property
    def is_success(self) -> bool:
        return self.status == "success"

    @property
    def is_failed(self) -> bool:
        return self.status in _FAILED_STATUSES


def _to_int(value: Any) -> Optional[int]:
    """Best-effort int conversion; ``None`` for anything that is not a plain number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, (float, str)):
        try:
            number = float(value)
        except ValueError:
            return None
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return int(number)
    return None


def _to_code(value: Any) -> Optional[int]:
    """Exact integer for error and status codes: 0.5 must not turn into 0 ("success")."""
    number = _to_int(value)
    if number is None:
        return None
    if isinstance(value, (float, str)) and float(value) != number:
        return None
    return number


def _is_http_url(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith(("http://", "https://"))


def _json_or_none(response: Any) -> Any:
    try:
        return response.json()
    except Exception:  # not JSON (HTML error page, empty body, ...)
        return None


def _parse_retry_after(response: Any) -> Optional[float]:
    """Seconds from a numeric Retry-After header, else ``None``."""
    try:
        value = response.headers.get("Retry-After")
    except Exception:
        return None
    if value is None:
        return None
    try:
        seconds = float(str(value).strip())
    except ValueError:
        return None
    if seconds != seconds or seconds < 0:
        return None
    return seconds


def _content_length(response: Any) -> Optional[int]:
    """Size announced by the Content-Length header, ``None`` if it is missing or unreadable."""
    try:
        value = response.headers.get("Content-Length")
    except Exception:
        return None
    return _to_int(value) if isinstance(value, (str, int)) else None


def _text_snippet(response: Any, limit: int = 200) -> str:
    try:
        text = str(response.text or "").strip()
    except Exception:
        return ""
    return text[:limit]


def _split_url(url: str) -> Optional[Any]:
    """``urlparse`` that returns ``None`` instead of raising for malformed URLs."""
    try:
        return urlparse(url)
    except ValueError:
        return None


def _host_and_port(parts: Any) -> str:
    """Host (and port) of a parsed URL without any user:password part."""
    host = parts.hostname or ""
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        port = None
    return f"{host}:{port}" if port else host


def _redact_url(url: str) -> str:
    """Scheme, host and path only: signed download URLs carry credentials in the query."""
    parts = _split_url(url)
    if parts is None:
        return "<malformed URL>"
    return f"{parts.scheme}://{_host_and_port(parts)}{parts.path}"


def _host_of(url: str) -> str:
    parts = _split_url(url)
    return (_host_and_port(parts) if parts is not None else "") or "<invalid URL>"


# Names of the urllib3 errors that prove the TCP connection was never established.
# NameResolutionError is a subclass of NewConnectionError; the checks are by name so the module
# does not depend on urllib3 internals, which differ between versions.
_PRE_SEND_ERROR_NAMES = frozenset({"NewConnectionError", "NameResolutionError", "ConnectTimeoutError"})
# Raised while the request is prepared or an adapter is chosen: nothing has been sent.
_PREPARE_ERRORS = (requests.exceptions.MissingSchema, requests.exceptions.InvalidSchema)
# requests raises these for the RESPONSE as well (a malformed header such as "Content-Length: 5, 7",
# a bad redirect Location), that is after the request went out: they prove nothing about it.
_EXCHANGE_ERRORS = (requests.exceptions.InvalidHeader, requests.exceptions.InvalidURL)
_CONFIG_ERRORS = _PREPARE_ERRORS + _EXCHANGE_ERRORS


def _exception_chain(exc: BaseException) -> list:
    """``exc`` and every exception wrapped inside it (args, ``reason``, cause, context)."""
    chain = []
    pending = [exc]
    seen = set()
    while pending and len(seen) < 30:
        err = pending.pop(0)
        if id(err) in seen:
            continue
        seen.add(id(err))
        chain.append(err)
        for nxt in (*err.args, getattr(err, "reason", None), err.__cause__, err.__context__):
            if isinstance(nxt, BaseException):
                pending.append(nxt)
    return chain


def _class_names(err: BaseException) -> frozenset:
    return frozenset(cls.__name__ for cls in type(err).__mro__)


# TLS failures that can only happen while the connection is being set up.  Other TLS errors
# (EOF, record-layer errors, garbage) can also hit after the request was sent.
_TLS_SETUP_ERROR_NAMES = frozenset({"SSLCertVerificationError", "CertificateError"})
_TLS_SETUP_TEXT = ("certificate verify failed", "certificate_verify_failed", "handshake")


def _tls_failed_before_send(chain: list) -> bool:
    if len(chain) == 1:
        return True  # an SSLError without any detail: assume it is the handshake
    for err in chain:
        if _class_names(err) & (_TLS_SETUP_ERROR_NAMES | _PRE_SEND_ERROR_NAMES):
            return True
        text = str(err).lower()
        if any(marker in text for marker in _TLS_SETUP_TEXT):
            return True
    return False


def _is_connect_failure(exc: BaseException) -> bool:
    """True when the failure proves that no part of the request reached Tripo.

    DNS failures, refused connections, unreachable networks, connect timeouts and failures of the
    TLS handshake (certificate problems, handshake alerts) qualify.  A reset or aborted
    connection, a read timeout, a broken body and a TLS error that is not clearly a handshake
    failure do not: the server may already have acted on the request.
    """
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return True
    if not isinstance(exc, requests.exceptions.ConnectionError):
        return False
    if isinstance(exc, requests.exceptions.ProxyError):
        return False
    chain = _exception_chain(exc)
    if isinstance(exc, requests.exceptions.SSLError):
        return _tls_failed_before_send(chain)
    for err in chain:
        if isinstance(err, (socket.gaierror, ConnectionRefusedError)):
            return True
        if _class_names(err) & _PRE_SEND_ERROR_NAMES:
            return True
    return False



# Quality options of the v3 generation endpoints (image-to-model and multiview-to-model), from the
# Tripo docs ("Multiview to 3D Model - H Series"). Only valid with model v3.0-20250812 or newer.
# Highest quality: geometry_quality "detailed" (Ultra mode, +20 credits) and texture_quality
# "detailed" (+10 credits) or "extreme" (8K textures, more credits, very large files). compress
# "geometry" (meshopt) keeps the file small; the app's model viewer decodes it.
QUALITY_OPTIONS: Dict[str, Tuple[str, Tuple[Any, ...]]] = {
    "geometry_quality": ("TRIPO_GEOMETRY_QUALITY", ("standard", "detailed")),
    "texture_quality": ("TRIPO_TEXTURE_QUALITY", ("fast", "standard", "detailed", "extreme")),
    "pbr": ("TRIPO_PBR", (True, False)),
    "texture_alignment": ("TRIPO_TEXTURE_ALIGNMENT", ("original_image", "geometry")),
    "compress": ("TRIPO_COMPRESS", ("geometry",)),
}


def _check_quality(options: Dict[str, Any]) -> Dict[str, Any]:
    checked = {}
    for name, value in options.items():
        if name not in QUALITY_OPTIONS:
            raise TripoInputError(f"unknown Tripo quality option {name!r}")
        allowed = QUALITY_OPTIONS[name][1]
        if value not in allowed or isinstance(value, bool) != isinstance(allowed[0], bool):
            raise TripoInputError(f"{name} must be one of {', '.join(map(str, allowed))}")
        checked[name] = value
    return checked


def quality_from_env(env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Quality options set in the environment; unset or invalid ones are left out (Tripo default)."""
    env = os.environ if env is None else env
    options: Dict[str, Any] = {}
    for name, (variable, allowed) in QUALITY_OPTIONS.items():
        raw = (env.get(variable) or "").strip().lower()
        if not raw:
            continue
        value: Any = raw
        if isinstance(allowed[0], bool):
            value = {"true": True, "1": True, "yes": True, "false": False, "0": False, "no": False}.get(raw)
        if value in allowed and isinstance(value, bool) == isinstance(allowed[0], bool):
            options[name] = value
        else:
            print(f"Предупреждение: {variable}={raw!r} не поддерживается Tripo, параметр не отправляется.")
    return options


class TripoClient:
    """Tripo API v3 client.  See the module docstring for the retry policy."""

    DEFAULT_BASE_URL = DEFAULT_BASE_URL
    CHINA_BASE_URL = CHINA_BASE_URL
    DEFAULT_MODEL = DEFAULT_MODEL

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        face_limit: Optional[int] = None,
        quality: Optional[Dict[str, Any]] = None,
        session: Optional[Any] = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        request_timeout: float = 30,
        upload_timeout: float = 120,
        download_timeout: float = 180,
    ) -> None:
        """Create a client.  Raises ``TripoError`` at once if the API key is empty or malformed."""
        key = api_key.strip() if isinstance(api_key, str) else ""
        if not key:
            raise TripoError("TRIPO_API_KEY is empty", hint=_HINT_AUTH)
        if not all(33 <= ord(ch) <= 126 for ch in key):
            raise TripoError(
                "TRIPO_API_KEY contains characters that cannot be part of an API key "
                "(spaces, line breaks or non-ASCII letters)",
                hint=_HINT_AUTH,
            )
        self._api_key = key

        if base_url is not None and not isinstance(base_url, str):
            raise TripoInputError("base_url must be a string")
        cleaned_url = (base_url or "").strip().rstrip("/")
        if any(ch.isspace() or ord(ch) < 32 for ch in cleaned_url):
            raise TripoInputError("base_url must not contain whitespace or control characters", hint=_HINT_URL)
        self._base_url = cleaned_url or DEFAULT_BASE_URL
        url_parts = _split_url(self._base_url)
        if url_parts is not None and url_parts.scheme == "http" and (url_parts.hostname or "") not in (
            "localhost", "127.0.0.1", "::1"
        ):
            logger.warning("The Tripo base URL uses plain http: the API key would be sent unencrypted")

        self.model = (model or "").strip() or DEFAULT_MODEL
        if face_limit is not None:
            face_limit = _to_int(face_limit)
            if face_limit is None or face_limit <= 0:
                raise TripoInputError("face_limit must be a positive integer")
        self.face_limit = face_limit
        self.quality = _check_quality(quality or {})

        self._session = session if session is not None else requests.Session()
        self._sleep = sleep
        self._monotonic = monotonic
        self.request_timeout = request_timeout
        self.upload_timeout = upload_timeout
        self.download_timeout = download_timeout
        self.max_download_bytes = _MAX_DOWNLOAD_BYTES
        self.max_download_seconds = _MAX_DOWNLOAD_SECONDS

    @classmethod
    def from_env(cls, **kwargs: Any) -> "TripoClient":
        """Build a client from the environment.

        TRIPO_API_KEY is required.  TRIPO_REGION selects the host: ``global`` (default) or
        ``china`` / ``cn``; an unknown value prints a warning and means ``global``.
        TRIPO_BASE_URL (a trailing slash is ignored) overrides the region.  TRIPO_MODEL and
        TRIPO_FACE_LIMIT are optional; an invalid face limit is ignored.  The quality options come from
        TRIPO_GEOMETRY_QUALITY, TRIPO_TEXTURE_QUALITY, TRIPO_PBR, TRIPO_TEXTURE_ALIGNMENT and
        TRIPO_COMPRESS (see ``QUALITY_OPTIONS``); an invalid value is ignored with a warning.  Extra keyword
        arguments go to the constructor.
        """
        region = (os.getenv("TRIPO_REGION") or "").strip().lower()
        if region and region not in _REGION_URLS:
            print(
                f"Предупреждение: неизвестное значение TRIPO_REGION={region!r} "
                "(допустимо: global, china, cn). Используется global."
            )
        base_url = (os.getenv("TRIPO_BASE_URL") or "").strip().rstrip("/") or _REGION_URLS.get(
            region, DEFAULT_BASE_URL
        )
        face_limit = None
        raw_limit = (os.getenv("TRIPO_FACE_LIMIT") or "").strip()
        if raw_limit:
            parsed = _to_int(raw_limit)
            if parsed is not None and parsed > 0:
                face_limit = parsed
        params: Dict[str, Any] = {
            "api_key": os.getenv("TRIPO_API_KEY", ""),
            "base_url": base_url,
            "model": os.getenv("TRIPO_MODEL") or None,
            "face_limit": face_limit,
            "quality": quality_from_env(),
        }
        params.update(kwargs)
        return cls(**params)

    @property
    def base_url(self) -> str:
        """The base URL all API calls go to."""
        return self._base_url

    # ------------------------------------------------------------------ public API

    def upload_image(self, data: bytes, filename: str, mime: str = "image/jpeg") -> str:
        """Upload a JPEG/PNG photo and return its ``file_token``."""
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise TripoInputError("Image data is empty")
        if len(data) > _MAX_UPLOAD_BYTES:
            raise TripoInputError("Image is larger than the 20 MB limit of Tripo")
        payload = self._call(
            "POST",
            "/files",
            idempotent=True,
            timeout=self.upload_timeout,
            files={"file": (filename, bytes(data), mime)},
        )
        token = payload.get("file_token")
        if not isinstance(token, str) or not token:
            raise TripoError("Tripo upload answered without a file_token")
        return token

    def create_image_to_model(self, file_token: str) -> str:
        """Start an image-to-model task for one uploaded photo and return the task id."""
        body = self._generation_body({"input": self._check_token(file_token, "file_token")})
        return self._create("/generation/image-to-model", body)

    def create_multiview_to_model(self, views: Dict[str, str]) -> str:
        """Start a multiview-to-model task and return the task id.

        ``views`` maps view names (front, left, back, right) to file tokens.  ``front`` is
        required and at least two views are needed.  Views whose token is ``None`` or blank
        are treated as omitted.
        """
        if not isinstance(views, dict):
            raise TripoInputError("views must be a dict such as {'front': token, 'left': token}")
        unknown = [str(key) for key in views if key not in VIEW_KEYS]
        if unknown:
            raise TripoInputError(
                "Unknown view(s): " + ", ".join(unknown) + "; allowed: " + ", ".join(VIEW_KEYS)
            )
        tokens: Dict[str, str] = {}
        for key in VIEW_KEYS:
            value = views.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            tokens[key] = self._check_token(value, f"view '{key}'")
        if "front" not in tokens:
            raise TripoInputError("multiview-to-model requires a 'front' view")
        if len(tokens) < 2:
            raise TripoInputError("multiview-to-model requires at least 2 views")
        body = self._generation_body({"inputs": [{key: token} for key, token in tokens.items()]})
        return self._create("/generation/multiview-to-model", body)

    def get_task(self, task_id: str) -> TripoTask:
        """Fetch the current state of a task."""
        tid = self._check_task_id(task_id)
        payload = self._call(
            "GET",
            f"/tasks/{quote(tid, safe='')}",
            idempotent=True,
            timeout=self.request_timeout,
        )
        return self._parse_task(payload, tid)

    def wait_for_task(
        self,
        task_id: str,
        poll_interval: float = 2.0,
        timeout: float = 900.0,
        on_progress: Optional[Callable[[int, str], None]] = None,
    ) -> TripoTask:
        """Poll until the task has a model URL.

        Returns the finished ``TripoTask``.  Raises ``TripoError`` if the task failed, was
        cancelled, finished without a model URL, did not finish within ``timeout`` seconds, or
        the status endpoint failed for 3 minutes in a row.  Statuses other than the documented
        ones are neither success nor failure: polling continues until ``timeout``.
        Transient poll failures (network errors, 429, 5xx after the retries of ``get_task``)
        are tolerated until no poll has succeeded for 3 minutes; permanent errors
        (401/403/404, other 4xx) raise at once.
        ``on_progress(progress, status)`` is called after every successful poll.
        """
        tid = self._check_task_id(task_id)
        try:
            interval, limit = float(poll_interval), float(timeout)
        except (TypeError, ValueError):
            raise TripoInputError("poll_interval and timeout must be numbers") from None
        if not (math.isfinite(interval) and math.isfinite(limit)) or interval < 0 or limit < 0:
            raise TripoInputError("poll_interval and timeout must be finite and not negative")
        interval = max(interval, _MIN_POLL_INTERVAL)
        start = self._monotonic()
        last_ok = start  # time of the last successful poll
        failures = 0  # consecutive failed polls: they lengthen the pause
        polls_without_url = 0
        reported_unknown = set()
        last_status, last_progress = "unknown", 0

        def pause(seconds: float) -> None:
            remaining = limit - (self._monotonic() - start)
            if remaining <= 0:
                raise TripoError(
                    f"Timed out after {limit:g} s waiting for Tripo task {tid} "
                    f"(last status: {last_status!r}, progress {last_progress}%)",
                    hint=_HINT_TASK_PENDING,
                )
            self._sleep(min(seconds, remaining))  # never sleep past the deadline

        while True:
            try:
                task = self.get_task(tid)
            except TripoError as err:
                if not err.transient:
                    raise
                silent_for = self._monotonic() - last_ok
                if silent_for >= _MAX_OUTAGE:
                    raise TripoError(
                        f"Lost contact with Tripo while waiting for task {tid} "
                        f"(no successful poll for {silent_for:.0f} s): {err}",
                        err.code,
                        err.http_status,
                        err.request_id,
                        err.suggestion,
                        err.hint,
                        transient=True,
                    ) from err
                failures += 1
                pause(min(max(interval, 1.0) * (2 ** failures), _MAX_POLL_PAUSE))
                continue
            failures = 0
            last_ok = self._monotonic()
            last_status, last_progress = task.status, task.progress
            self._notify(on_progress, task)

            if task.is_success:
                if task.model_url:
                    return task
                if polls_without_url >= _MAX_POLLS_WITHOUT_URL:
                    raise TripoError(
                        f"Tripo task {tid} succeeded but returned no model URL",
                        hint=_HINT_TASK_PENDING,
                    )
                polls_without_url += 1
            elif task.is_failed:
                detail = task.error_message or "no details provided"
                code_note = f" (task error_code {task.error_code})" if task.error_code is not None else ""
                raise TripoError(
                    f"Tripo task {tid} ended with status '{task.status}': {detail}{code_note}",
                    hint=_HINT_TASK_FAILED,
                    task_error_code=task.error_code,
                )
            elif task.status not in ("queued", "running") and task.status not in reported_unknown:
                reported_unknown.add(task.status)
                logger.warning(
                    "Tripo task %s reported status %r, which is not documented; "
                    "waiting for a final status",
                    tid,
                    task.status,
                )
            pause(interval)

    def download(self, url: str) -> bytes:
        """Download a GLB from a (signed) URL and return its bytes.

        No credentials are sent.  Raises ``TripoError`` unless the answer is HTTP 200 with a
        complete GLB: the body must start with the magic bytes ``glTF`` and must not be shorter
        than the total length declared in the GLB header.
        """
        parts = _split_url(url) if isinstance(url, str) else None
        if parts is None or parts.scheme not in ("http", "https") or not parts.netloc:
            raise TripoInputError("Refusing to download: not an http(s) URL")
        return self._with_retries(lambda: self._download_once(url), lambda err: err.transient)

    @staticmethod
    def extract_model_url(output: Optional[Dict[str, Any]]) -> Optional[str]:
        """Return the model link from a task's ``output`` object, or ``None``.

        Only http(s) strings count, so a version string such as "v3.1-20260211" is never
        mistaken for a URL.
        """
        if not isinstance(output, dict):
            return None
        for key in _MODEL_URL_KEYS:
            value = output.get(key)
            if _is_http_url(value):
                return value.strip()
        return None

    # ------------------------------------------------------------------ internals

    def _generation_body(self, body: Dict[str, Any]) -> Dict[str, Any]:
        body["model"] = self.model
        if self.face_limit is not None:
            body["face_limit"] = self.face_limit
        body.update(self.quality)
        return body

    @staticmethod
    def _check_token(value: Any, what: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise TripoInputError(f"{what} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _check_task_id(task_id: Any) -> str:
        if not isinstance(task_id, str) or not task_id.strip():
            raise TripoInputError("task_id must be a non-empty string")
        return task_id.strip()

    def _create(self, path: str, body: Dict[str, Any]) -> str:
        try:
            payload = self._call(
                "POST",
                path,
                idempotent=False,
                timeout=self.request_timeout,
                json_body=body,
            )
        except TripoError as err:
            if err.maybe_created:
                err.message += "; the task may still have been created (check the Tripo dashboard before submitting again)"
                err.args = (err.message,)
            raise
        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            # The server answered 2xx but gave no task id: the task may exist and be billed.
            raise TripoError(
                "Tripo accepted the request but returned no task_id; the task may still have been created",
                maybe_created=True,
            )
        return task_id

    @staticmethod
    def _parse_task(data: Dict[str, Any], task_id: str) -> TripoTask:
        output = data.get("output") if isinstance(data.get("output"), dict) else None
        rendered = None
        if output is not None:
            for key in ("rendered_image_url", "rendered_image"):
                if _is_http_url(output.get(key)):
                    rendered = output[key].strip()
                    break
        progress = _to_int(data.get("progress")) or 0
        message = data.get("error_message")
        return TripoTask(
            task_id=str(data.get("task_id") or task_id),
            status=data.get("status"),  # normalised by TripoTask: non-strings become "unknown"
            progress=max(0, min(100, progress)),
            model_url=TripoClient.extract_model_url(output),
            rendered_image_url=rendered,
            error_code=_to_code(data.get("error_code")),
            error_message=str(message) if message is not None else None,
            raw=data,
        )

    @staticmethod
    def _notify(callback: Optional[Callable[[int, str], None]], task: TripoTask) -> None:
        if callback is None:
            return
        try:
            callback(task.progress, task.status)
        except Exception:  # a broken progress printer must not abort the wait for a paid task
            logger.debug("on_progress callback failed", exc_info=True)

    def _call(
        self,
        method: str,
        path: str,
        *,
        idempotent: bool,
        timeout: float,
        json_body: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """One logical API call: the retries wrap a single attempt."""
        if idempotent:
            retry_if = lambda err: err.transient  # noqa: E731
        else:
            # Task creation is paid: repeat it only after a 429, which means "refused, nothing done".
            retry_if = lambda err: err.http_status == 429  # noqa: E731
        return self._with_retries(
            lambda: self._attempt(method, path, timeout, json_body, files),
            retry_if,
        )

    def _with_retries(self, operation: Callable[[], Any], retry_if: Callable[[TripoError], bool]) -> Any:
        """Run ``operation`` up to 3 times while ``retry_if`` accepts the error.

        The wait before a retry is the back-off step (1, 2, 4 ... s) or the Retry-After value of the
        server, whichever is larger, so ``Retry-After: 0`` still backs off.  The total wait is
        capped, so a huge Retry-After raises instead of sleeping.
        """
        attempt = 0
        waited = 0.0
        while True:
            try:
                return operation()
            except TripoError as err:
                attempt += 1
                if attempt >= _MAX_ATTEMPTS or not retry_if(err):
                    raise
                delay = _BACKOFF_STEPS[min(attempt - 1, len(_BACKOFF_STEPS) - 1)]
                if err.retry_after is not None:
                    delay = max(delay, err.retry_after)
                if waited + delay > _MAX_TOTAL_WAIT:
                    raise TripoError(
                        f"{err.message}; not retrying: already waited {waited:g} s and the next wait "
                        f"of {delay:g} s would exceed the {_MAX_TOTAL_WAIT:g} s limit",
                        err.code,
                        err.http_status,
                        err.request_id,
                        err.suggestion,
                        err.hint,
                        transient=err.transient,
                        maybe_created=err.maybe_created,
                        retry_after=err.retry_after,
                    ) from err
                self._sleep(delay)
                waited += delay

    def _attempt(
        self,
        method: str,
        path: str,
        timeout: float,
        json_body: Optional[Dict[str, Any]],
        files: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Send the request once and turn every failure into a ``TripoError``."""
        # Sent per request (never stored on the session), so it cannot leak anywhere else.
        headers = {"Authorization": f"Bearer {self._api_key}"}
        url = self._base_url + path
        host = _host_of(self._base_url)
        try:
            response = self._send(method, url, headers, timeout, json_body, files)
        except _PREPARE_ERRORS as exc:
            # No scheme / no adapter for the URL: raised before anything is sent.
            raise TripoError(
                f"Invalid request for {_redact_url(url)} ({type(exc).__name__})", hint=_HINT_URL
            ) from exc
        except _EXCHANGE_ERRORS as exc:
            # The same classes also report a malformed RESPONSE (header, redirect target), which means
            # the request went out: for a paid create the task may exist.
            raise TripoError(
                f"Malformed request or response for {_redact_url(url)} ({type(exc).__name__})",
                hint=_HINT_URL,
                maybe_created=True,
            ) from exc
        except requests.exceptions.RequestException as exc:
            if _is_connect_failure(exc):
                # Nothing reached Tripo: say so plainly.
                raise TripoError(
                    f"Could not reach Tripo at {host} ({type(exc).__name__})",
                    transient=True,
                    hint=_HINT_NETWORK,
                ) from exc
            raise TripoError(
                f"Request to {host} failed ({type(exc).__name__})",
                transient=True,
                maybe_created=True,  # the request may have been sent before the failure
                hint=_HINT_NETWORK,
            ) from exc
        except (OSError, ValueError) as exc:
            # Local problems that requests does not wrap: an unusable CA bundle (OSError),
            # a header value that cannot be encoded (UnicodeEncodeError).  Nothing was sent.
            raise TripoError(
                f"Cannot send the request to {host} ({type(exc).__name__})", hint=_HINT_LOCAL
            ) from exc
        return self._unwrap(response)

    def _send(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        timeout: float,
        json_body: Optional[Dict[str, Any]],
        files: Optional[Dict[str, Any]],
    ) -> Any:
        read_timeout = float(timeout)
        pair = (min(_CONNECT_TIMEOUT, read_timeout), read_timeout)
        # Never follow redirects here: after a 307/308 requests would send the paid POST (and the
        # Authorization header) again.  A 3xx answer is turned into a TripoError by _unwrap.
        if method == "POST":
            kwargs: Dict[str, Any] = {"headers": headers, "timeout": pair, "allow_redirects": False}
            if json_body is not None:
                kwargs["json"] = json_body
            if files is not None:
                kwargs["files"] = files
            return self._session.post(url, **kwargs)
        return self._session.get(url, headers=headers, timeout=pair, allow_redirects=False)

    @staticmethod
    def _unwrap(response: Any) -> Dict[str, Any]:
        """Return ``data`` of a successful envelope; raise ``TripoError`` for anything else."""
        status = response.status_code
        body = _json_or_none(response)
        envelope = body if isinstance(body, dict) else None
        code = _to_code(envelope.get("code")) if envelope is not None else None

        if 300 <= status < 400:
            # Redirects are never followed for API calls, and a redirect means the request was not
            # processed (so a create is not "maybe created").
            raise TripoError(
                f"Unexpected redirect (HTTP {status}) from Tripo API; redirects are not followed for API calls",
                http_status=status,
                hint=_HINT_URL,
            )
        if 200 <= status < 300:
            if envelope is not None and code == 0:
                data = envelope.get("data")
                return data if isinstance(data, dict) else {}
            if envelope is None or code is None:
                # A proxy page or an empty body: the request may have been processed, and a safe
                # call (poll, upload) is worth repeating.
                raise TripoError(
                    "Unexpected answer from Tripo API (no valid response envelope)",
                    http_status=status,
                    transient=True,
                    maybe_created=True,
                )
            # HTTP 2xx carrying an error envelope: a definite rejection.

        suggestion = request_id = None
        if envelope is not None:
            message = str(envelope.get("message") or f"HTTP {status}")
            suggestion = envelope.get("suggestion")
            request_id = envelope.get("request_id")
        else:
            snippet = _text_snippet(response)
            message = f"HTTP {status}" + (f": {snippet}" if snippet else "")
        transient = status == 429 or status >= 500
        # A 4xx envelope from the API (or any 429) is a definite refusal.  A 5xx, or an answer that
        # is not an API envelope at all (proxy page, 408, ...), leaves it open whether the request
        # was processed.
        maybe_created = status >= 500 or (envelope is None and status != 429)
        raise TripoError(
            message,
            code,
            status,
            str(request_id) if request_id is not None else None,
            str(suggestion) if suggestion is not None else None,
            transient=transient,
            maybe_created=maybe_created,
            retry_after=_parse_retry_after(response) if transient else None,
        )

    def _download_once(self, url: str) -> bytes:
        shown = _redact_url(url)
        read_timeout = float(self.download_timeout)
        started = self._monotonic()
        # Errors below are raised "from None": the original exceptions of requests/urllib3 quote
        # the full signed URL, which must not end up in a traceback.
        try:
            # No headers on purpose: the signed URL must not receive the API key.
            response = self._session.get(
                url, stream=True, timeout=(min(_CONNECT_TIMEOUT, read_timeout), read_timeout)
            )
        except requests.exceptions.RequestException as exc:
            raise TripoError(
                f"Cannot download the model from {shown} ({type(exc).__name__})",
                transient=not isinstance(exc, _CONFIG_ERRORS),
                hint=_HINT_NETWORK,
            ) from None
        except (OSError, ValueError) as exc:
            raise TripoError(
                f"Cannot download the model from {shown} ({type(exc).__name__})", hint=_HINT_LOCAL
            ) from None
        buffer = bytearray()  # one growing buffer, not a list of chunks plus a joined copy
        try:
            status = response.status_code
            if status != 200:
                transient = status == 429 or status >= 500
                raise TripoError(
                    f"Model download from {shown} failed",
                    http_status=status,
                    transient=transient,
                    retry_after=_parse_retry_after(response) if transient else None,
                )
            limit_text = f"{self.max_download_bytes // (1024 * 1024)} MB"
            announced = _content_length(response)  # refuse a huge file before reading any of it
            if announced is not None and announced > self.max_download_bytes:
                raise TripoError(f"Model file from {shown} is larger than {limit_text}")
            try:
                for chunk in response.iter_content(chunk_size=_DOWNLOAD_CHUNK_BYTES):
                    if not chunk:
                        continue
                    buffer += chunk
                    if len(buffer) > self.max_download_bytes:
                        raise TripoError(f"Model file from {shown} is larger than {limit_text}")
                    if self._monotonic() - started > self.max_download_seconds:
                        # download_timeout only bounds each read: a trickle must not hold the pipeline.
                        # (A read returns once a whole chunk has arrived, hence the small chunk size.)
                        raise TripoError(
                            f"Model download from {shown} took longer than {self.max_download_seconds:g} s"
                        )
            except (OSError, ValueError) as exc:  # OSError includes every requests exception
                raise TripoError(
                    f"Model download from {shown} was interrupted ({type(exc).__name__})",
                    transient=True,
                    hint=_HINT_NETWORK,
                ) from None
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # closing a finished response must not hide the real outcome
                    logger.debug("closing the download response failed", exc_info=True)
        if not buffer:
            raise TripoError(f"Model file from {shown} is empty")
        if not buffer.startswith(_GLB_MAGIC):
            raise TripoError(f"File from {shown} is not a GLB model (missing 'glTF' header)")
        if len(buffer) < _GLB_HEADER_BYTES:
            raise TripoError(f"Model file from {shown} is truncated: the GLB header is incomplete")
        declared = int.from_bytes(buffer[8:12], "little")
        if declared > len(buffer):
            raise TripoError(
                f"Model file from {shown} is truncated: the GLB header declares {declared} bytes, "
                f"{len(buffer)} received"
            )
        return bytes(buffer)
