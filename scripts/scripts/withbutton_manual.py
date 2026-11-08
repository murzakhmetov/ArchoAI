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

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', 'archoai_app', '.env'))
load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'archoai_app', '.env'))
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

PROJECT_REF = os.getenv("SUPABASE_PROJECT_REF")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BUCKET_NAME = os.getenv("SUPABASE_BUCKET_NAME", "artifact-images")
MODEL_BUCKET_NAME = os.getenv("SUPABASE_MODEL_BUCKET_NAME", "3d1")

SUPABASE_DB_URL = f"https://{PROJECT_REF}.supabase.co/rest/v1/artifacts"
SUPABASE_STORAGE_URL = f"https://{PROJECT_REF}.supabase.co/storage/v1/object/{BUCKET_NAME}"
SUPABASE_MODEL_STORAGE_URL = f"https://{PROJECT_REF}.supabase.co/storage/v1/object/{MODEL_BUCKET_NAME}"

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
    cap = cv2.VideoCapture(2, cv2.CAP_V4L2)
    if not cap.isOpened():
        cap = cv2.VideoCapture(2)
    if not cap.isOpened():
        raise Exception("Не удалось открыть веб-камеру")

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

    response = requests.post(upload_url, headers=headers, data=image_bytes)

    if response.status_code not in [200, 201]:
        raise Exception(f"Ошибка загрузки в Storage (Код {response.status_code}): {response.text}")

    public_url = f"https://{PROJECT_REF}.supabase.co/storage/v1/object/public/{BUCKET_NAME}/{file_name}"
    print(f"Фото успешно загружено. Ссылка: {public_url}")
    return public_url


TRIPO_API_KEY = os.getenv("TRIPO_API_KEY")
TRIPO_BASE_URL = "https://api.tripo3d.ai/v2/openapi"
TRIPO_FALLBACK_URL = "https://api.tripo3d.com/v2/openapi"

UPLOAD_GLB_TO_SUPABASE = os.getenv("UPLOAD_GLB_TO_SUPABASE", "true").lower() == "true"


def generate_3d_model_with_tripo(images_bytes_list):
    if not TRIPO_API_KEY:
        print("TRIPO_API_KEY не найден в .env. Пропуск генерации 3D-модели.")
        return None

    if isinstance(images_bytes_list, bytes):
        images_bytes_list = [images_bytes_list]

    num_photos = len(images_bytes_list)
    print(f"\n[3.5/5] Запуск генерации 3D-модели через Tripo API ({num_photos} ракурс(ов))...")
    headers = {"Authorization": f"Bearer {TRIPO_API_KEY}"}

    print(f"1. Загрузка {num_photos} фото в Tripo...")
    file_tokens = []

    for idx, img_bytes in enumerate(images_bytes_list):
        files = {"file": (f"artifact_view_{idx+1}.jpg", img_bytes, "image/jpeg")}
        upload_res = None
        for base_url in [TRIPO_BASE_URL, TRIPO_FALLBACK_URL]:
            try:
                upload_res = requests.post(f"{base_url}/upload", headers=headers, files=files, timeout=30)
                if upload_res.status_code == 200:
                    break
            except Exception as e:
                print(f"Ошибка запроса к {base_url}: {e}")

        if not upload_res or upload_res.status_code != 200:
            print(f"Ошибка загрузки изображения {idx+1} в Tripo: {upload_res.text if upload_res else 'Нет ответа'}")
            return None

        upload_data = upload_res.json()
        if upload_data.get("code") != 0:
            print(f"Ошибка Tripo API при загрузке фото {idx+1}: {upload_data.get('message')}")
            return None

        image_token = upload_data["data"].get("image_token") or upload_data["data"].get("file_token")
        file_tokens.append({"type": "jpg", "file_token": image_token})
        print(f"     Фото {idx+1}/{num_photos} загружено в Tripo.")

    if len(file_tokens) > 1:
        print("2. Создание задачи генерации 3D-модели из нескольких ракурсов (multiview_to_model)...")
        task_payload = {
            "type": "multiview_to_model",
            "files": file_tokens
        }
    else:
        print("2. Создание задачи генерации 3D-модели (image_to_model)...")
        task_payload = {
            "type": "image_to_model",
            "file": file_tokens[0]
        }

    create_task_res = requests.post(f"{TRIPO_BASE_URL}/task", headers=headers, json=task_payload)

    if create_task_res.status_code != 200 or create_task_res.json().get("code") != 0:
        err_msg = create_task_res.text if create_task_res.status_code != 200 else create_task_res.json().get("message")
        print(f"Предупреждение: Ошибка создания задачи multiview ({err_msg}).")
        if len(file_tokens) > 1:
            print("  Пробуем резервный вариант с 1-м снимком (image_to_model)...")
            fallback_payload = {
                "type": "image_to_model",
                "file": file_tokens[0]
            }
            create_task_res = requests.post(f"{TRIPO_BASE_URL}/task", headers=headers, json=fallback_payload)
            if create_task_res.status_code != 200 or create_task_res.json().get("code") != 0:
                print(f"Ошибка создания резервной задачи Tripo: {create_task_res.text}")
                return None

    task_data = create_task_res.json()
    task_id = task_data["data"]["task_id"]
    print(f"Задача 3D создана. Task ID: {task_id}")

    print("3. Генерация 3D сетки и текстур (это может занять 1-2 минуты)...")
    start_time = time.time()
    model_tripo_url = None

    while time.time() - start_time < 300:
        time.sleep(5)
        status_res = requests.get(f"{TRIPO_BASE_URL}/task/{task_id}", headers=headers)
        if status_res.status_code != 200:
            continue

        st_json = status_res.json()
        if st_json.get("code") != 0:
            continue

        res_data = st_json.get("data", {})
        status = res_data.get("status", "").lower()
        progress = res_data.get("progress", 0)

        print(f"    Статус: {status.upper()} | Прогресс: {progress}%", end="\r")

        if status == "success":
            print("\nГенерация 3D модели в Tripo завершена успешно!")

            def find_model(obj):
                if isinstance(obj, dict):
                    url = obj.get("model") or obj.get("model_url") or obj.get("glb") or obj.get("pbr_model") or obj.get("result_url")
                    if url:
                        return url
                    for v in obj.values():
                        res = find_model(v)
                        if res:
                            return res
                elif isinstance(obj, list):
                    for item in obj:
                        res = find_model(item)
                        if res:
                            return res
                return None

            model_tripo_url = find_model(res_data)
            break

        elif status in ["failed", "cancelled", "banned"]:
            print(f"\nГенерация 3D модели провалилась со статусом: {status}")
            return None

    if not model_tripo_url:
        print("\nНе удалось получить ссылку на 3D модель из Tripo.")
        return None

    if not UPLOAD_GLB_TO_SUPABASE:
        print("Режим экономии памяти Supabase Storage (UPLOAD_GLB_TO_SUPABASE=False). Использована прямая ссылка Tripo.")
        return model_tripo_url

    print(f"4. Перенос 3D GLB файла в Supabase Storage (бакет '{MODEL_BUCKET_NAME}')...")
    try:
        glb_bytes = requests.get(model_tripo_url).content
        model_filename = f"artifact_model_{int(time.time())}.glb"
        upload_url = f"{SUPABASE_MODEL_STORAGE_URL}/{model_filename}"

        storage_headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "model/gltf-binary"
        }

        up_res = requests.post(upload_url, headers=storage_headers, data=glb_bytes)
        if up_res.status_code in [200, 201]:
            public_model_url = f"https://{PROJECT_REF}.supabase.co/storage/v1/object/public/{MODEL_BUCKET_NAME}/{model_filename}"
            print(f"3D модель сохранена в Supabase Storage (бакет '{MODEL_BUCKET_NAME}')!\nПубличная ссылка: {public_model_url}")
            return public_model_url
        else:
            print(f"Не удалось загрузить GLB в Supabase бакет '{MODEL_BUCKET_NAME}' (Код {up_res.status_code}): {up_res.text}")
            return model_tripo_url
    except Exception as e:
        print(f"Ошибка при переносе GLB файла в Supabase: {e}")
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

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=original_bytes, mime_type='image/jpeg'),
            prompt
        ]
    )

    result_text = response.text.strip()
    if result_text.startswith("```"):
        result_text = result_text.split("```")[1]
        if result_text.startswith("json"):
            result_text = result_text[4:]

    print("Ответ от Gemini получен.")
    return json.loads(result_text.strip())


def save_all_to_supabase(gemini_data, image_url, model_url=None):
    print("\n[5/5] Сохранение агрегированных данных в таблицу public.artifacts...")

    final_payload = gemini_data
    final_payload["image_path"] = image_url
    if model_url:
        final_payload["model_url"] = model_url
    final_payload["status"] = "complete" if model_url else "pending"

    if "crack_percentage" in final_payload:
        final_payload["crack_percentage"] = round(float(final_payload["crack_percentage"]), 2)
    else:
        final_payload["crack_percentage"] = 0.0

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }

    print(f"Payload для отправки в БД:\n{json.dumps(final_payload, indent=2, ensure_ascii=False)}")

    response = requests.post(SUPABASE_DB_URL, headers=headers, json=final_payload)

    if response.status_code in [200, 201]:
        print("\nУСПЕХ! Все данные, фото и 3D-модель сохранены в Supabase!")
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
