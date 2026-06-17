import cv2
import numpy as np
import requests
import json
import time
import torch
from google import genai
from google.genai import types

from util.inference_utils import create_model, inference
from main import Parameters

GEMINI_API_KEY = "AIzaSyD4csAoJpeQO5sOsIjujaeGyU44-hrOXnA"
GEMINI_MODEL = "gemini-3.1-flash-lite"  # Используем стабильную версию для строгого следования JSON-схеме

PROJECT_REF = "wvhdhfddtusppjmsgmvn"
SUPABASE_KEY = "sb_publishable_rHDFfoFz_v9zDNKk9o64Gw_Pc-eZvln"
BUCKET_NAME = "artifact-images"

SUPABASE_DB_URL = f"https://{PROJECT_REF}.supabase.co/rest/v1/artifacts"
SUPABASE_STORAGE_URL = f"https://{PROJECT_REF}.supabase.co/storage/v1/object/{BUCKET_NAME}"


print("Инициализация API клиента Gemini и локальной ML модели...")
client = genai.Client(api_key=GEMINI_API_KEY)

params = Parameters()
params.gpu_ids = [] 
crack_model = create_model(params)
print("Все нейросети успешно загружены и готовы к работе!")


def capture_image_from_webcam():
    print("\n[1/5] Включение веб-камеры...")
    cap = cv2.VideoCapture(2, cv2.CAP_V4L2)  
    
    if not cap.isOpened():
        raise Exception("Не удалось открыть веб-камеру")
        
    for _ in range(15):  
        cap.read()
        
    ret, frame = cap.read()
    cap.release()
    
    if not ret:
        raise Exception("Не удалось сделать снимок с веб-камеры")
        
    _, buffer = cv2.imencode('.jpg', frame)
    print("Оригинальное изображение успешно захвачено.")
    return buffer.tobytes()


def analyze_cracks_with_ml(original_bytes):
    print("\n[2/5] Обработка изображения локальной ML-моделью для разметки...")
    try:
        result_img, visuals = inference(crack_model, original_bytes, (256, 256), "px")
        _, encoded_buffer = cv2.imencode('.jpg', result_img)
        print("Изображение успешно размечено локальной ML-моделью.")
        return encoded_buffer.tobytes()
        
    except (ZeroDivisionError, IndexError, TypeError, NameError) as e:
        print(f"⚠️ Локальная ML-модель не смогла разметить трещины ({e}). Используем чистое фото для бакета.")
        return original_bytes
    except Exception as e:
        print(f"⚠️ Предупреждение: Непредвиденная ошибка локальной ML-модели ({e}). Используем чистое фото.")
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


def save_all_to_supabase(gemini_data, image_url):
    print("\n[5/5] Сохранение агрегированных данных в таблицу public.artifacts...")
    
    final_payload = gemini_data
    final_payload["image_path"] = image_url
    final_payload["status"] = "pending"
    
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
        print("\n🎉 УСПЕХ! Все данные и фото сохранены в Supabase!")
    else:
        print(f"\n🚨 Ошибка Supabase при записи строки (Код {response.status_code}): {response.text}")


def start_pipeline():
    print("\n🟢 Запуск сканирования артефакта...")
    try:
        raw_photo_bytes = capture_image_from_webcam()
        analyzed_photo_bytes = analyze_cracks_with_ml(raw_photo_bytes)
        storage_image_url = upload_to_supabase_storage(analyzed_photo_bytes)
        artifact_info = analyze_artifact_with_gemini(raw_photo_bytes)
        save_all_to_supabase(artifact_info, storage_image_url)
    except Exception as e:
        print(f"\n❌ Критическая ошибка в конвейере: {e}")


if __name__ == "__main__":
    print("\n🚀 Скрипт успешно запущен")
    
    try:
        while True:
            input("\n👉 Нажмите [ENTER] в этом терминале, чтобы сделать снимок артефакта (или Ctrl+C для выхода)...")
            start_pipeline()
    except KeyboardInterrupt:
        print("\nРабота скрипта завершена.")