"""Store generated GLB models in the batched Supabase Storage buckets.

The project keeps models in buckets ``models-01`` .. ``models-04`` (225 MB soft capacity each, 50 MiB per file,
filled in order).
The server decides which bucket gets the next file (RPC ``pick_model_bucket``), and every stored file
is recorded for usage accounting (RPC ``register_model_file``).  Buckets do not add capacity: the
free quota is shared, so a refusal always means "full" and ends the attempt.  There is one exception:
an old project that does not have the RPC at all (HTTP 404, PostgREST ``PGRST202`` / ``42883``).
It has no quota logic to bypass, so the legacy bucket ``3d1`` (``fallback_bucket``) is used there.

Flow of ``store_model``:
  1. refuse an empty file or one over 50 MiB;
  2. pick the bucket: "storage full" / "too large" answers, network errors, 5xx and every other RPC
     answer end the attempt (nothing is uploaded); only a missing RPC selects the fallback bucket;
  3. upload once, into the picked bucket (or the fallback bucket when the RPC is missing); a refused
     upload ends the attempt, there is no second try in another bucket;
  4. register the file (best effort) and return the public URL.

The module imports only the standard library and ``requests`` (no torch / OpenCV / dotenv) and takes
the HTTP object (``session``) and the logger as arguments, so it can be tested without a network.
The API key is only ever sent in request headers; it is removed from everything that is logged.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

import requests

__all__ = ["MAX_MODEL_BYTES", "StoredModel", "clamp_crack_percentage", "store_model"]

MAX_MODEL_BYTES = 52428800  # 50 MiB: the size limit of every models-NN bucket

# PostgREST error codes of pick_model_bucket
_CODE_STORAGE_FULL = "53100"  # project cap reached or every batch is full
_CODES_REFUSED_SIZE = ("54000", "22023")  # file larger than 50 MB / invalid size
# The RPC itself does not exist (a project without the migrations): HTTP 404 or one of these codes
_CODES_RPC_MISSING = ("PGRST202", "42883")
# Outcomes of the bucket pick
_PICKED, _REFUSED, _MISSING, _FAILED = "picked", "refused", "missing", "failed"

# Object names must match this pattern or the bucket policies reject the upload.
_OBJECT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,198}[.]glb$")
# Bucket ids and the project ref end up in URLs: accept plain identifiers only.
_BUCKET_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")
_PROJECT_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}")


@dataclass
class StoredModel:
    """A model file that is now stored in Supabase Storage."""

    url: str  # public URL
    bucket_id: str
    object_path: str
    size_bytes: int
    sha256: str  # lowercase hex
    file_id: Optional[str] = None  # id from register_model_file; None if registration failed


def clamp_crack_percentage(value: Any) -> float:
    """Crack percentage as a float within 0.0..100.0 rounded to 2 decimals.

    The database rejects a whole artifact whose crack_percentage lies outside 0..100, so the value
    is clamped.  Missing, NaN, infinite and non-numeric values (booleans and text such as "abc"
    included) become 0.0; numeric text such as "7.5" is accepted.
    """
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return round(min(max(number, 0.0), 100.0), 2) + 0.0  # "+ 0.0" turns -0.0 into 0.0


def _say(log: Callable[[str], Any], message: str) -> None:
    try:
        log(message)
    except Exception:  # a broken logger (for example a console that cannot print Cyrillic) must not lose the upload
        pass


def _short(text: Any, secret: str, limit: int = 160) -> str:
    """One-line excerpt of ``text`` with the API key removed."""
    cleaned = str(text)
    if secret:
        cleaned = cleaned.replace(secret, "***")
    return cleaned.replace("\r", " ").replace("\n", " ").strip()[:limit]


def _exception_reason(exc: BaseException, secret: str) -> str:
    detail = _short(exc, secret)
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def _response_reason(response: Any, secret: str) -> str:
    detail = ""
    try:
        body = response.json()
        if isinstance(body, dict):
            detail = str(body.get("message") or body.get("error") or body.get("msg") or "")
    except Exception:
        pass
    if not detail:
        try:
            detail = str(getattr(response, "text", "") or "")
        except Exception:
            detail = ""
    detail = _short(detail, secret)
    status = getattr(response, "status_code", "?")
    return f"HTTP {status}" + (f": {detail}" if detail else "")


def _postgrest_code(response: Any) -> Optional[str]:
    try:
        body = response.json()
    except Exception:
        return None
    if isinstance(body, dict) and body.get("code") is not None:
        return str(body["code"]).strip()
    return None


def _is_ok(response: Any) -> bool:
    return getattr(response, "status_code", None) in (200, 201)


def _usable_key(api_key: Any) -> bool:
    """Non-empty printable ASCII: the key goes into HTTP headers."""
    return isinstance(api_key, str) and bool(api_key) and all(33 <= ord(ch) <= 126 for ch in api_key)


def _pick_bucket(http: Any, base: str, headers: dict, size: int, timeout: float, api_key: str,
                 log: Callable[[str], Any]) -> Tuple[Optional[str], str]:
    """Ask the server for a bucket.  Returns ``(bucket, outcome)``.

    ``_PICKED``: ``bucket`` is where the file goes.  ``_MISSING``: the RPC does not exist (HTTP 404,
    PostgREST ``PGRST202`` / ``42883``); this is the only case in which the caller may use the
    fallback bucket.  ``_REFUSED`` (storage full, file too large) and ``_FAILED`` (network error,
    timeout, 5xx, an odd answer, any other error) mean that nothing may be uploaded.
    """
    try:
        response = http.post(
            f"{base}/rest/v1/rpc/pick_model_bucket", headers=headers, json={"p_size_bytes": size}, timeout=timeout
        )
    except Exception as exc:
        _say(log, f"[Supabase] Бакет через RPC выбрать не удалось ({_exception_reason(exc, api_key)}): модель не сохранена.")
        return None, _FAILED

    status = getattr(response, "status_code", None)
    if status == 200:
        try:
            body = response.json()
        except Exception:
            body = None
        if isinstance(body, str) and _BUCKET_RE.fullmatch(body.strip()):
            return body.strip(), _PICKED
        _say(log, "[Supabase] RPC выбора бакета вернул неожиданный ответ: модель не сохранена.")
        return None, _FAILED

    code = _postgrest_code(response)
    if code == _CODE_STORAGE_FULL:
        _say(log, f"[Supabase] Хранилище заполнено (код {code}): модель не сохранена. Освободите место в Supabase Storage.")
        return None, _REFUSED
    if code in _CODES_REFUSED_SIZE:
        _say(log, f"[Supabase] Сервер отклонил размер файла (код {code}): модель не сохранена.")
        return None, _REFUSED
    if status == 404 or code in _CODES_RPC_MISSING:
        _say(log, "[Supabase] RPC выбора бакета не найден (проект без миграций).")
        return None, _MISSING
    _say(log, f"[Supabase] RPC выбора бакета ответил ошибкой ({_response_reason(response, api_key)}): модель не сохранена.")
    return None, _FAILED


def _upload(http: Any, base: str, bucket: str, object_path: str, data: bytes, headers: dict, timeout: float,
            api_key: str, log: Callable[[str], Any]) -> bool:
    try:
        response = http.post(
            f"{base}/storage/v1/object/{bucket}/{object_path}", headers=headers, data=data, timeout=timeout
        )
    except Exception as exc:
        _say(log, f"[Supabase] Загрузка в бакет '{bucket}' не удалась ({_exception_reason(exc, api_key)}).")
        return False
    if _is_ok(response):
        return True
    _say(log, f"[Supabase] Загрузка в бакет '{bucket}' не удалась ({_response_reason(response, api_key)}).")
    return False


def _register(http: Any, base: str, payload: dict, headers: dict, timeout: float, api_key: str,
              log: Callable[[str], Any]) -> Optional[str]:
    """Record the file for usage accounting.  Best effort: returns the file id or ``None``."""
    try:
        response = http.post(f"{base}/rest/v1/rpc/register_model_file", headers=headers, json=payload, timeout=timeout)
    except Exception as exc:
        _say(log, f"[Supabase] Предупреждение: файл не записан в учёт использования ({_exception_reason(exc, api_key)}).")
        return None
    if not _is_ok(response):
        _say(log, f"[Supabase] Предупреждение: файл не записан в учёт использования ({_response_reason(response, api_key)}).")
        return None
    try:
        body = response.json()
    except Exception:
        return None
    return body if isinstance(body, str) and body else None


def store_model(
    glb_bytes: bytes,
    project_ref: str,
    api_key: str,
    tripo_task_id: Optional[str] = None,
    fallback_bucket: Optional[str] = "3d1",
    source: str = "tripo",
    timeout: float = 60,
    session: Optional[Any] = None,
    log: Callable[[str], Any] = print,
) -> Optional[StoredModel]:
    """Upload a GLB model to Supabase Storage and return where it is, or ``None`` if it was not stored.

    ``session`` is any object with a requests-style ``post(url, headers=..., json=... | data=...,
    timeout=...)``; by default a ``requests.Session`` is used.  ``log`` receives one line at a time.
    ``fallback_bucket`` (``None`` for none) is used only when the bucket RPC does not exist at all
    (HTTP 404, PostgREST ``PGRST202`` / ``42883``: an old project without the migrations).  Every other
    RPC problem (network error, timeout, 5xx, an odd answer) and every refusal ("storage full", "too
    large", a refused upload) ends the attempt with ``None``: the quota is shared by all buckets, so
    falling back to the legacy bucket would bypass it.  The file is uploaded at most once.
    """
    if not isinstance(glb_bytes, (bytes, bytearray, memoryview)):
        _say(log, "[Supabase] Модель не сохранена: ожидались данные файла в виде байтов.")
        return None
    size = glb_bytes.nbytes if isinstance(glb_bytes, memoryview) else len(glb_bytes)
    if size == 0:
        _say(log, "[Supabase] Модель не сохранена: файл пустой.")
        return None
    if size > MAX_MODEL_BYTES:
        _say(log, f"[Supabase] Модель не сохранена: размер {size} байт больше лимита {MAX_MODEL_BYTES} байт (50 МБ).")
        return None
    if not isinstance(project_ref, str) or not _PROJECT_REF_RE.fullmatch(project_ref):
        _say(log, "[Supabase] Модель не сохранена: SUPABASE_PROJECT_REF не задан или записан неверно.")
        return None
    if not _usable_key(api_key):
        _say(log, "[Supabase] Модель не сохранена: ключ Supabase не задан или содержит недопустимые символы.")
        return None
    if fallback_bucket is not None and not (isinstance(fallback_bucket, str) and _BUCKET_RE.fullmatch(fallback_bucket)):
        _say(log, "[Supabase] Резервный бакет задан неверно: он использоваться не будет.")
        fallback_bucket = None

    data = glb_bytes if isinstance(glb_bytes, bytes) else bytes(glb_bytes)  # requests treats a bytearray as a stream
    sha256 = hashlib.sha256(data).hexdigest()
    object_path = f"artifact_model_{int(time.time())}_{sha256[:8]}.glb"
    if not _OBJECT_NAME_RE.fullmatch(object_path):  # cannot happen; the bucket policy would reject it anyway
        _say(log, "[Supabase] Модель не сохранена: недопустимое имя файла.")
        return None

    base = f"https://{project_ref}.supabase.co"
    auth = {"apikey": api_key, "Authorization": f"Bearer {api_key}"}
    json_headers = dict(auth, **{"Content-Type": "application/json"})
    upload_headers = dict(auth, **{"Content-Type": "model/gltf-binary"})

    owns_session = session is None
    http = requests.Session() if owns_session else session
    try:
        bucket, outcome = _pick_bucket(http, base, json_headers, size, timeout, api_key, log)
        if outcome == _MISSING:
            if fallback_bucket is None:
                _say(log, "[Supabase] Модель не сохранена: RPC выбора бакета нет, а резервный бакет не задан.")
                return None
            bucket = fallback_bucket
            _say(log, f"[Supabase] Используем резервный бакет '{bucket}'.")
        elif outcome == _PICKED:
            _say(log, f"[Supabase] Выбран бакет '{bucket}'.")
        else:  # refused or failed: _pick_bucket has said why, and nothing may be uploaded
            return None

        # One attempt only.  A bucket that refuses the file means "full": another bucket would bypass the quota.
        if not _upload(http, base, bucket, object_path, data, upload_headers, timeout, api_key, log):
            return None

        payload = {"p_bucket_id": bucket, "p_object_path": object_path, "p_sha256": sha256}
        if source:
            payload["p_source"] = source
        if tripo_task_id:
            payload["p_tripo_task_id"] = str(tripo_task_id)
        file_id = _register(http, base, payload, json_headers, timeout, api_key, log)

        return StoredModel(
            url=f"{base}/storage/v1/object/public/{bucket}/{object_path}",
            bucket_id=bucket,
            object_path=object_path,
            size_bytes=size,
            sha256=sha256,
            file_id=file_id,
        )
    finally:
        if owns_session:
            http.close()
