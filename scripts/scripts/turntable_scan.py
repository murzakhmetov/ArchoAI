"""Turntable scan: press SPACE (or R), the NEMA 17 turns the artifact in 60 degree steps and the webcam takes a
photo at every step (6 photos, one full turn). Then everything goes to the ArchoAI server on the
Raspberry Pi AND to the Supabase cloud project (each gets its own copy, same artifact id), ready for
the mobile app:

  * the crack-marked main photo (DeepCrack) -> artifacts.image_path,
  * all 6 angle photos -> artifacts.photo_urls,
  * a 3D model from 4 of the views (Tripo, needs TRIPO_API_KEY) -> artifacts.model_url,
  * name, type, material, era, purpose, condition, crack percentage (Gemini) -> the other columns.

    cd scripts/scripts && ./venv/bin/python turntable_scan.py

Keys in the terminal: SPACE (or R) starts a scan (the whole chain: 6 photos on the turntable, crack detection,
upload, Tripo 3D model, Gemini analysis, database), Q (or Ctrl+C) ends the script.
Settings (archoai_app/.env or the environment): SUPABASE_URL, SUPABASE_KEY (the Pi),
SUPABASE_FALLBACK_URL, SUPABASE_FALLBACK_KEY (the cloud; empty: the Pi only), GEMINI_API_KEY,
TRIPO_API_KEY, CAMERA_INDEX, TURNTABLE_PORT (default: the Arduino found on USB),
SCAN_STEPS (default 6 photos per turn), SCAN_SETTLE_SECONDS (pause after each move, default 0.8).
Hardware: Arduino Uno + CNC Shield V3 + NEMA 17 with the firmware turntable/turntable.ino.
"""
import os
import select
import sys
import termios
import time
import tty
import uuid

import cv2
import requests

# The scanner pipeline (DeepCrack, Tripo, Gemini, server upload) lives in withbutton.py; importing it
# loads the configuration and the models once.
import withbutton as pipeline
from backend_select import load_server_config
from supabase_models import clamp_crack_percentage, store_model
from tripo_client import TripoClient, TripoError
from turntable import Turntable

SCAN_STEPS = int(os.getenv("SCAN_STEPS") or 6)
STEP_DEGREES = 360.0 / SCAN_STEPS
SETTLE_SECONDS = float(os.getenv("SCAN_SETTLE_SECONDS") or 0.8)
# Tripo multiview takes up to four views (front, left, back, right): the photos closest to
# 0, 90, 180 and 270 degrees are used (with 6 steps: 0, 60 or 120, 180, 240 or 300).
TRIPO_ANGLES = (0, 90, 180, 270)


def tripo_views(photos):
    """The photos nearest to front / left / back / right, in that order (no duplicates)."""
    chosen = []
    for target in TRIPO_ANGLES:
        best = min(range(len(photos)), key=lambda i: abs(((i * STEP_DEGREES - target) + 180) % 360 - 180))
        if best not in chosen:
            chosen.append(best)
    return [photos[i] for i in chosen]


def open_camera():
    cap = cv2.VideoCapture(pipeline.CAMERA_INDEX, cv2.CAP_V4L2)
    if not cap.isOpened():
        cap = cv2.VideoCapture(pipeline.CAMERA_INDEX)
    if not cap.isOpened():
        raise RuntimeError(f"Не удалось открыть веб-камеру {pipeline.CAMERA_INDEX} (CAMERA_INDEX в .env)")
    for _ in range(15):  # warm-up: exposure and white balance settle
        cap.read()
    return cap


def take_photo(cap):
    for _ in range(5):  # drop the frames buffered while the table was turning
        cap.grab()
    ok, frame = cap.read()
    if not ok or frame is None:
        raise RuntimeError("Камера не вернула кадр")
    ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise RuntimeError("Не удалось сжать кадр в JPEG")
    return buffer.tobytes()


def capture_turn(table, cap):
    """One full turn: photo, turn by one step, ... The table ends where it started."""
    print(f"\n[1/5] Съёмка: {SCAN_STEPS} фото, поворот по {STEP_DEGREES:g}°")
    photos = []
    for i in range(SCAN_STEPS):
        time.sleep(SETTLE_SECONDS)
        photos.append(take_photo(cap))
        print(f"  Фото {i + 1}/{SCAN_STEPS} ({i * STEP_DEGREES:g}°) снято. Поворот на {STEP_DEGREES:g}°...")
        table.rotate(STEP_DEGREES)
    print(f"  Полный оборот выполнен, стол вернулся в начальное положение.")
    return photos


def servers():
    """Where every scan is saved: BOTH configured servers, the one the scripts selected first
    (SUPABASE_URL / SUPABASE_KEY, e.g. the Pi) and the second one (SUPABASE_FALLBACK_URL /
    SUPABASE_FALLBACK_KEY, e.g. the cloud project). Each gets its own copy of the photos, the 3D
    model and the artifact row under the same artifact id, so server/replicate.py (when it runs)
    sees one artifact on both sides and copies nothing twice."""
    targets = [(f"{'Supabase cloud' if '.supabase.c' in pipeline.SUPABASE_URL else 'Raspberry Pi'}",
                pipeline.SUPABASE_URL, pipeline.SUPABASE_KEY)]
    try:
        config = load_server_config()
    except Exception:
        return targets
    for server in (config.primary, config.fallback):
        if server is not None and server.url != pipeline.SUPABASE_URL:
            targets.append(("Supabase cloud" if server.is_cloud else "Raspberry Pi", server.url, server.key))
    return targets


def make_3d_model(views):
    """Tripo multiview model from up to 4 views. Returns (glb bytes, task id) or (None, task id or None)."""
    if not pipeline.TRIPO_API_KEY:
        print("\n[3.5/5] TRIPO_API_KEY не задан: 3D-модель пропущена.")
        return None, None
    print(f"\n[3.5/5] 3D-модель через Tripo ({len(views)} ракурса)...")
    task_id = None
    try:
        client = TripoClient.from_env()
        tokens = [client.upload_image(v, f"view_{i + 1}.jpg", "image/jpeg") for i, v in enumerate(views)]
        try:
            task_id = client.create_multiview_to_model(dict(zip(pipeline.TRIPO_VIEW_ORDER, tokens)))
        except TripoError as exc:
            # Only an explicit refusal (4xx, not 429) is retried with one photo: anything else may have
            # created a paid task already.
            refused = exc.http_status is not None and 400 <= exc.http_status < 500 and exc.http_status != 429 \
                and not exc.maybe_created and exc.code not in pipeline.TRIPO_NO_FALLBACK_CODES
            if not refused:
                raise
            print(f"  Multiview отклонён ({exc}); пробуем один снимок (image_to_model)...")
            task_id = client.create_image_to_model(tokens[0])
        print(f"  Задача Tripo: {task_id}. Генерация (1-3 минуты)...")
        task = client.wait_for_task(task_id, timeout=pipeline.TRIPO_WAIT_TIMEOUT_SECONDS,
                                    on_progress=pipeline.print_tripo_progress)
        glb = client.download(task.model_url)  # Tripo links are temporary: keep the file itself
        print(f"\n  3D-модель готова ({len(glb) / 1e6:.1f} МБ).")
        return glb, task_id
    except TripoError as exc:
        print(f"\n  Ошибка Tripo: {exc}" + (f"\n  Подсказка: {exc.hint}" if exc.hint else ""))
        if task_id:
            print(f"  Task ID: {task_id} (модель можно найти в панели Tripo).")
        return None, task_id


def upload_photo(base_url, key, image_bytes, path):
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "image/jpeg"}
    response = requests.post(f"{base_url}/storage/v1/object/{pipeline.BUCKET_NAME}/{path}", headers=headers,
                             data=image_bytes, timeout=60)
    if response.status_code not in (200, 201):
        raise RuntimeError(f"загрузка {path}: код {response.status_code} {response.text[:200]}")
    return f"{base_url}/storage/v1/object/public/{pipeline.BUCKET_NAME}/{path}"


def save_to_server(name, base_url, key, scan_id, artifact_id, analyzed, photos, glb, task_id, info):
    """Photos, model and the artifact row on one server. Returns a short result line."""
    image_url = upload_photo(base_url, key, analyzed, f"scans/{scan_id}/analyzed.jpg")
    photo_urls = [upload_photo(base_url, key, photo, f"scans/{scan_id}/angle_{int(i * STEP_DEGREES):03d}.jpg")
                  for i, photo in enumerate(photos)]
    model_url = None
    if glb is not None:
        stored = store_model(glb, base_url, key, tripo_task_id=task_id, fallback_bucket=pipeline.MODEL_BUCKET_NAME,
                             log=lambda line: print("  " + line))
        model_url = stored.url if stored else None
    row = dict(info, id=artifact_id, image_path=image_url, photo_urls=photo_urls, status="complete" if model_url else "pending",
               crack_percentage=clamp_crack_percentage(info.get("crack_percentage")))
    if model_url:
        row["model_url"] = model_url
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "Prefer": "return=minimal"}
    response = requests.post(f"{base_url}/rest/v1/artifacts", headers=headers, json=row, timeout=60)
    note = ""
    if response.status_code == 400 and "photo_urls" in response.text:
        # A project without migration 20261128000001 has no photo_urls column: the photos are uploaded,
        # the row is stored without the list.
        row.pop("photo_urls")
        response = requests.post(f"{base_url}/rest/v1/artifacts", headers=headers, json=row, timeout=60)
        note = ", без списка ракурсов (нет столбца photo_urls)"
    if response.status_code not in (200, 201):
        raise RuntimeError(f"запись в artifacts: код {response.status_code} {response.text[:200]}")
    return f"{len(photo_urls)} фото, 3D-модель {'есть' if model_url else 'нет'}{note}"


def unanalyzed_info(scan_id):
    """Artifact fields for a scan that Gemini could not analyze."""
    return {"name": f"Скан {scan_id[:15]} (без анализа)", "type": "Unknown", "material": "Unknown",
            "era": "Unknown", "purpose": "Unknown", "condition": "Not analyzed", "crack_percentage": 0.0}


def scan(table, cap):
    photos = capture_turn(table, cap)
    scan_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    artifact_id = str(uuid.uuid4())  # the same id on every server
    analyzed = pipeline.analyze_cracks_with_ml(photos[0])  # prints [2/5]
    glb, task_id = make_3d_model(tripo_views(photos))  # prints [3.5/5]
    try:
        info = pipeline.analyze_artifact_with_gemini(photos[0])  # prints [4/5]
    except Exception as exc:
        # Gemini stayed busy on every model: the photos and the model must not be lost. The artifact
        # is saved as "pending" with placeholder data; edit the details later in the app.
        print(f"  Gemini недоступен ({str(exc)[:160]}).\n  Скан сохраняется без описания (статус pending).")
        info = unanalyzed_info(scan_id)
    print(f"\n[5/5] Сохранение: {info.get('name', '?')}")
    results = []
    for name, base_url, key in servers():
        try:
            line = save_to_server(name, base_url, key, scan_id, artifact_id, analyzed, photos, glb, task_id, info)
            print(f"  {name}: сохранено ({line}).")
            results.append(True)
        except Exception as exc:  # the other server still gets its copy
            print(f"  {name}: ОШИБКА: {exc}")
            results.append(False)
    if all(results):
        print("\nУСПЕХ! Скан сохранён на всех серверах.")
    elif any(results):
        print("\nСкан сохранён не везде (см. выше).")
    else:
        print("\nСкан не сохранён ни на одном сервере.")


class KeyReader:
    """Single key presses without ENTER. The terminal stays in that mode for the whole session (keys
    typed early are not lost); keys pressed during a scan are dropped (an extra SPACE/R starts no second scan)."""

    def __init__(self):
        self.fd = sys.stdin.fileno() if sys.stdin.isatty() else None
        self.saved = None

    def __enter__(self):
        if self.fd is not None:
            self.saved = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
        return self

    def __exit__(self, *exc):
        if self.saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)

    def drop_pending(self):
        if self.fd is not None:
            termios.tcflush(self.fd, termios.TCIFLUSH)

    def wait(self):
        """Returns 'r' to start a scan (SPACE or R), 'q' to quit."""
        if self.fd is None:  # not a terminal (piped input): one line per command
            line = sys.stdin.readline()
            return "q" if not line or line.strip().lower() == "q" else "r"
        while True:
            ready, _, _ = select.select([self.fd], [], [], 0.5)
            if not ready:
                continue
            keys = os.read(self.fd, 16).decode("utf-8", "ignore").lower()
            for key in keys:
                if key in (" ", "r", "к"):  # SPACE or R (also R with the Russian keyboard layout)
                    return "r"
                if key in ("q", "й", "\x1b"):
                    return "q"


def main():
    with Turntable() as table:
        print(f"Поворотный стол: {table.port} ({table.hello})")
        cap = open_camera()
        try:
            with KeyReader() as keys:
                while True:
                    keys.drop_pending()
                    print("\nПоставьте артефакт на стол и нажмите ПРОБЕЛ (или R) для сканирования (Q - выход)...", flush=True)
                    if keys.wait() == "q":
                        break
                    try:
                        scan(table, cap)
                    except Exception as exc:  # one failed scan must not end the session
                        print(f"\nОшибка сканирования: {exc}")
        except KeyboardInterrupt:
            pass
        finally:
            cap.release()
            try:
                table.off()
            except Exception:
                pass
    print("\nРабота завершена.")


if __name__ == "__main__":
    main()
