import cv2
import sys
import os

def check_cameras(max_index=5):
    print("🔍 Поиск и проверка доступных веб-камер...\n")
    found_cameras = []
    
    output_dir = os.path.join(os.path.dirname(__file__), "camera_test_photos")
    os.makedirs(output_dir, exist_ok=True)

    for index in range(max_index):
        # Пробуем с фолбэками бэкенда V4L2 и по умолчанию
        cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap = cv2.VideoCapture(index)

        if cap.isOpened():
            # Прогрев веб-камеры
            for _ in range(10):
                cap.read()

            ret, frame = cap.read()
            if ret and frame is not None:
                height, width, _ = frame.shape
                filename = f"camera_index_{index}.jpg"
                filepath = os.path.abspath(os.path.join(output_dir, filename))
                cv2.imwrite(filepath, frame)
                
                print(f"✅ Камера по индексу [{index}] НАЙДЕНА!")
                print(f"   • Разрешение: {width}x{height}")
                print(f"   • Путь к фото: {filepath}\n")
                found_cameras.append(index)
            else:
                print(f"⚠️ Камера по индексу [{index}] открыта, но не удалось получить кадр.\n")
            
            cap.release()
        else:
            print(f"❌ Камера по индексу [{index}] не найдена.")

    print("-" * 50)
    if found_cameras:
        print(f"🎉 Итого найдено доступных камер: {len(found_cameras)}")
        print(f"Папка со снимками: {os.path.abspath(output_dir)}")
        print(f"Доступные индексы: {found_cameras}")
    else:
        print("❌ Не найдено ни одной подключенной камеры.")

if __name__ == "__main__":
    max_idx = 5
    if len(sys.argv) > 1:
        try:
            max_idx = int(sys.argv[1])
        except ValueError:
            pass
    check_cameras(max_idx)
