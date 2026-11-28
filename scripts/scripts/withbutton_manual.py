import cv2
import numpy as np
import requests
import json
import time
import torch
import os
from dotenv import load_dotenv
from google import genai
from google.genai import types

from util.inference_utils import create_model, inference
from main import Parameters
from tripo_client import TripoClient, TripoError
import gemini_call
from supabase_models import store_model, clamp_crack_percentage
from backend_select import server_from_env

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', 'archoai_app', '.env'))
load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'archoai_app', '.env'))
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

# Сервер базы данных и хранилища: Supabase на Raspberry Pi (SUPABASE_URL, например
# http://192.168.1.61:54321, см. server/README.md). Нужен сетевой адрес Pi, а не 127.0.0.1: ссылки на
# фото и модели сохраняются в базе и должны открываться на телефоне. SUPABASE_PROJECT_REF без
# SUPABASE_URL по-прежнему означает облачный проект Supabase.
# Второй сервер (SUPABASE_FALLBACK_URL + SUPABASE_FALLBACK_KEY, например облако при сервере на Pi)
# используется, когда первый не отвечает; server/replicate.py на Pi копирует данные между ними.
_SERVER = server_from_env()
SUPABASE_URL = _SERVER.url if _SERVER else None
SUPABASE_KEY = _SERVER.key if _SERVER else ""
BUCKET_NAME = os.getenv("SUPABASE_BUCKET_NAME", "artifact-images")
MODEL_BUCKET_NAME = os.getenv("SUPABASE_MODEL_BUCKET_NAME", "3d1")

if SUPABASE_URL is None or not SUPABASE_KEY:
    raise SystemExit(
        "Не задан адрес сервера или ключ: укажите SUPABASE_URL (например http://192.168.1.61:54321) "
        "и SUPABASE_KEY (publishable key сервера) в archoai_app/.env"
    )

SUPABASE_DB_URL = f"{SUPABASE_URL}/rest/v1/artifacts"
SUPABASE_STORAGE_URL = f"{SUPABASE_URL}/storage/v1/object/{BUCKET_NAME}"

# Номер веб-камеры зависит от компьютера (одна USB-камера на Raspberry Pi это 0): доступные номера
# показывает check_camera.py. Задаётся через CAMERA_INDEX в .env или в окружении: номер (0, 1, ...) или путь
# устройства, например /dev/v4l/by-id/usb-...-video-index0 (не меняется при переподключении камеры).
_CAMERA_SETTING = (os.getenv("CAMERA_INDEX") or "").strip()
CAMERA_INDEX = int(_CAMERA_SETTING) if _CAMERA_SETTING.isdigit() else (_CAMERA_SETTING or 2)

print("Инициализация API клиента Gemini и локальной ML модели...")
client = genai.Client(api_key=GEMINI_API_KEY)

params = Parameters()
params.gpu_ids = []
crack_model = create_model(params)
print("Все нейросети успешно загружены и готовы к работе!")


def capture_single_photo(cap, index, total):
    print(f"  Подготовка камеры к снимку {index}/{total}...")
    for _ in range(15):
        cap.read()
    ret, frame = cap.read()
    if not ret:
        raise Exception(f"Не удалось сделать снимок {index} с веб-камеры")
    _, buffer = cv2.imencode('.jpg', frame)
    print(f"  Снимок {index}/{total} успешно захвачен.")
    return buffer.tobytes()


def capture_images_manually(count=4):
    print(f"\n[1/5] Включение веб-камеры. Требуется {count} ракурса.")
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)
    if not cap.isOpened():
        cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        raise Exception(
            f"Не удалось открыть веб-камеру с номером {CAMERA_INDEX}. "
            "Найдите номер через check_camera.py и задайте CAMERA_INDEX в .env"
        )

    photos = []
    for i in range(count):
        input(f"\nНажмите [ENTER] чтобы сделать снимок {i+1} из {count}...")
        photo = capture_single_photo(cap, i + 1, count)
        photos.append(photo)

    cap.release()
    print(f"\nВсе {count} ракурса успешно захвачены.")
    return photos


def analyze_cracks_with_ml(original_bytes):
    print("\n[2/5] Обработка изображения локальной ML-моделью для разметки...")
    try:
        result_img, visuals = inference(crack_model, original_bytes, (256, 256), "px")
        _, encoded_buffer = cv2.imencode('.jpg', result_img)
        print("Изображение успешно размечено локальной ML-моделью.")
        return encoded_buffer.tobytes()
    except (ZeroDivisionError, IndexError, TypeError, NameError) as e:
        print(f"Локальная ML-модель не смогла разметить трещины ({e}). Используем чистое фото для бакета.")
        return original_bytes
    except Exception as e:
        print(f"Предупреждение: Непредвиденная ошибка локальной ML-модели ({e}). Используем чистое фото.")
        return original_bytes


def upload_to_supabase_storage(image_bytes):
    file_name = f"artifact_analyzed_{int(time.time())}.jpg"
    upload_url = f"{SUPABASE_STORAGE_URL}/{file_name}"

    print(f"\n[3/5] Загрузка изображения {file_name} в бакет Supabase...")

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "image/jpeg"
    }

    response = requests.post(upload_url, headers=headers, data=image_bytes, timeout=60)

    if response.status_code not in [200, 201]:
        raise Exception(f"Ошибка загрузки в Storage (Код {response.status_code}): {response.text}")

    public_url = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET_NAME}/{file_name}"
    print(f"Фото успешно загружено. Ссылка: {public_url}")
    return public_url


TRIPO_API_KEY = os.getenv("TRIPO_API_KEY")

UPLOAD_GLB_TO_SUPABASE = os.getenv("UPLOAD_GLB_TO_SUPABASE", "true").lower() == "true"

# Tripo multiview_to_model принимает до 4 ракурсов: снимки по порядку съёмки -> front, left, back, right
TRIPO_VIEW_ORDER = ("front", "left", "back", "right")
# Ошибки ключа и кредитов: резервный вариант (image_to_model) упадёт точно так же
TRIPO_NO_FALLBACK_CODES = (2, 1002, 2010)
# Сколько секунд ждать готовую модель: раньше было 300 с, а клиент по умолчанию ждёт 900 с (15 минут
# блокировки интерактивного сканирования)
TRIPO_WAIT_TIMEOUT_SECONDS = 600
TRIPO_TEMP_LINK_WARNING = (
    "Внимание: ссылки Tripo временные и скоро перестанут работать. "
    "Для постоянного хранения включите UPLOAD_GLB_TO_SUPABASE=true."
)
# Флаг UPLOAD_GLB_TO_SUPABASE уже включён, но копию в Supabase сделать не удалось
TRIPO_COPY_FAILED_WARNING = (
    "Внимание: копия модели в Supabase не создана, сохранена прямая ссылка Tripo. "
    "Эта ссылка временная и скоро перестанет работать."
)


def print_tripo_progress(progress, status):
    print(f"    Статус: {str(status).upper()} | Прогресс: {progress}%", end="\r")


def generate_3d_model_with_tripo(images_bytes_list):
    if not TRIPO_API_KEY:
        print("TRIPO_API_KEY не найден в .env. Пропуск генерации 3D-модели.")
        return None

    if isinstance(images_bytes_list, bytes):
        images_bytes_list = [images_bytes_list]

    if not images_bytes_list:
        print("Нет фотографий для генерации 3D-модели. Пропуск.")
        return None

    if len(images_bytes_list) > len(TRIPO_VIEW_ORDER):
        extra = len(images_bytes_list) - len(TRIPO_VIEW_ORDER)
        print(f"Tripo принимает не более {len(TRIPO_VIEW_ORDER)} ракурсов: лишние снимки ({extra} шт.) игнорируются.")
        images_bytes_list = images_bytes_list[:len(TRIPO_VIEW_ORDER)]

    num_photos = len(images_bytes_list)
    print(f"\n[3.5/5] Запуск генерации 3D-модели через Tripo API ({num_photos} ракурс(ов))...")

    task_id = None
    try:
        client = TripoClient.from_env()

        print(f"1. Загрузка {num_photos} фото в Tripo...")
        file_tokens = []
        for idx, img_bytes in enumerate(images_bytes_list):
            file_tokens.append(client.upload_image(img_bytes, f"artifact_view_{idx+1}.jpg", "image/jpeg"))
            print(f"     Фото {idx+1}/{num_photos} загружено в Tripo.")

        if len(file_tokens) > 1:
            print("2. Создание задачи генерации 3D-модели из нескольких ракурсов (multiview_to_model)...")
            try:
                task_id = client.create_multiview_to_model(dict(zip(TRIPO_VIEW_ORDER, file_tokens)))
            except TripoError as e:
                # Резервный вариант (image_to_model) пробуем только после явного отказа API: ответ 4xx
                # (кроме 429), разобранный клиентом (maybe_created=False). Любой другой исход (5xx,
                # таймаут, обрыв, 429, непонятный ответ) мог создать задачу: вторая задача спишет
                # кредиты повторно. Ошибки ключа и кредитов тоже не повторяем: упадёт так же.
                explicit_rejection = (
                    e.http_status is not None and 400 <= e.http_status < 500 and e.http_status != 429
                    and not e.maybe_created
                )
                if not explicit_rejection or e.code in TRIPO_NO_FALLBACK_CODES:
                    raise
                print(f"Предупреждение: Ошибка создания задачи multiview ({e}).")
                print("  Пробуем резервный вариант с 1-м снимком (image_to_model)...")
        else:
            print("2. Создание задачи генерации 3D-модели (image_to_model)...")

        if task_id is None:
            task_id = client.create_image_to_model(file_tokens[0])
        print(f"Задача 3D создана. Task ID: {task_id}")

        print("3. Генерация 3D сетки и текстур (это может занять 1-2 минуты)...")
        task = client.wait_for_task(task_id, timeout=TRIPO_WAIT_TIMEOUT_SECONDS, on_progress=print_tripo_progress)
        print("\nГенерация 3D модели в Tripo завершена успешно!")
        model_tripo_url = task.model_url
    except TripoError as e:
        print(f"\nОшибка Tripo API: {e}")
        if e.hint:
            print(f"  Подсказка: {e.hint}")
        if task_id:
            print(f"  Task ID: {task_id} (результат можно найти в панели Tripo).")
        elif e.maybe_created:
            print("  Tripo мог принять задачу, хотя ответ не получен. Повторный запрос не отправлялся, чтобы не списать кредиты дважды.")
        print("Генерация 3D-модели пропущена.")
        return None
    except Exception as e:
        print(f"\nНепредвиденная ошибка при работе с Tripo: {e}")
        return None

    if not UPLOAD_GLB_TO_SUPABASE:
        print("Режим экономии памяти Supabase Storage (UPLOAD_GLB_TO_SUPABASE=False). Использована прямая ссылка Tripo.")
        print(TRIPO_TEMP_LINK_WARNING)
        return model_tripo_url

    print(f"4. Перенос 3D GLB файла в Supabase Storage (бакет выбирает сервер; '{MODEL_BUCKET_NAME}' только для проекта без выбора бакетов)...")
    try:
        # Ссылки Tripo подписаны и временные: забираем GLB сразу после завершения задачи.
        # Если скачать не удалось, остаётся прямая ссылка Tripo (как и раньше).
        glb_bytes = client.download(model_tripo_url)
        # fallback_bucket работает только если на проекте нет RPC выбора бакетов (старый проект без миграций).
        # Если хранилище заполнено или бакет отказал в загрузке, модель не сохраняется: квота общая для всех бакетов.
        stored = store_model(
            glb_bytes, SUPABASE_URL, SUPABASE_KEY, tripo_task_id=task_id, fallback_bucket=MODEL_BUCKET_NAME
        )
        if stored is not None:
            print(f"3D модель сохранена в Supabase Storage (бакет '{stored.bucket_id}')!\nПубличная ссылка: {stored.url}")
            return stored.url
        print(TRIPO_COPY_FAILED_WARNING)
        return model_tripo_url
    except Exception as e:
        print(f"Ошибка при переносе GLB файла в Supabase: {e}")
        print(TRIPO_COPY_FAILED_WARNING)
        return model_tripo_url


def analyze_artifact_with_gemini(original_bytes):
    print("\n[4/5] Отправка ОРИГИНАЛЬНОГО изображения в Gemini API...")

    prompt = (
        "You are a world-class archaeologist. Analyze this image of a historical artifact with maximum precision.\n"
        "CRITICAL INSTRUCTION: Be concise, definitive, and strictly single-valued. Do not use 'or', slashes (/), or multiple options. "
        "Choose the single most likely variant based on visual evidence.\n\n"
        "Extract characteristics strictly in JSON format:\n"
        "- name: A brief, definitive name of the artifact (string, maximum 4 words. Example: 'Terracotta vessel fragment')\n"
        "- type: A single main category, e.g., 'Domestic', 'Ritual', 'Military', 'Numismatics' (string, exactly 1 word)\n"
        "- material: Primary material composition, e.g., 'Terracotta', 'Bronze', 'Iron', 'Clay' (string, maximum 2 words)\n"
        "- era: Specific probable historical period, culture, or century. "
        "Choose ONE definitive period. Examples: 'Iron Age', 'Karakhanid Period', 'Golden Horde', 'Bronze Age'. NEVER return 'Unknown'. (string)\n"
        "- purpose: The primary historical use of the object (string, maximum 5 words. Example: 'Ceremonial vessel component')\n"
        "- condition: Current physical state and preservation level (string, maximum 3 words. Example: 'Fragmentary surface wear')\n"
        "- crack_percentage: Precise visual estimate of damage/cracks from 0.0 to 100.0 (float).\n\n"
        "Return ONLY the JSON object. No preambles, no markdowns, no backticks."
    )

    # Retries and other models when Gemini is busy (503 "high demand"): see gemini_call.py
    response = gemini_call.generate(
        client,
        GEMINI_MODEL,
        [types.Part.from_bytes(data=original_bytes, mime_type='image/jpeg'), prompt],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    print("Ответ от Gemini получен.")
    return gemini_call.parse_json_object(response.text)


def save_all_to_supabase(gemini_data, image_url, model_url=None):
    print("\n[5/5] Сохранение агрегированных данных в таблицу public.artifacts...")

    final_payload = gemini_data
    final_payload["image_path"] = image_url
    if model_url:
        final_payload["model_url"] = model_url
    final_payload["status"] = "complete" if model_url else "pending"

    # В БД crack_percentage должен быть в пределах 0..100, иначе вставка всей записи отклоняется
    final_payload["crack_percentage"] = clamp_crack_percentage(final_payload.get("crack_percentage"))

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }

    print(f"Payload для отправки в БД:\n{json.dumps(final_payload, indent=2, ensure_ascii=False)}")

    response = requests.post(SUPABASE_DB_URL, headers=headers, json=final_payload, timeout=60)

    if response.status_code in [200, 201]:
        if model_url:
            print("\nУСПЕХ! Данные, фото и 3D-модель сохранены на сервере!")
        else:
            print("\nУСПЕХ! Данные и фото сохранены на сервере (3D-модель не создана).")
    else:
        print(f"\nОшибка Supabase при записи строки (Код {response.status_code}): {response.text}")


def start_pipeline():
    print("\nЗапуск сканирования артефакта (4 ракурса, ручной режим)...")
    try:
        raw_photos = capture_images_manually(count=4)
        primary_photo_bytes = raw_photos[0]

        analyzed_photo_bytes = analyze_cracks_with_ml(primary_photo_bytes)
        storage_image_url = upload_to_supabase_storage(analyzed_photo_bytes)
        model_url = generate_3d_model_with_tripo(raw_photos)
        artifact_info = analyze_artifact_with_gemini(primary_photo_bytes)
        save_all_to_supabase(artifact_info, storage_image_url, model_url)
    except Exception as e:
        print(f"\nОшибка в конвейере: {e}")


if __name__ == "__main__":
    print("\nСкрипт успешно запущен (ручной режим по ENTER)")

    try:
        while True:
            input("\nНажмите [ENTER] чтобы начать новое сканирование (или Ctrl+C для выхода)...")
            start_pipeline()
    except KeyboardInterrupt:
        print("\nРабота скрипта завершена.")
