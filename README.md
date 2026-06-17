# 🏛️ ArchoAI - AI-Powered Archaeological Conservation

ArchoAI is a comprehensive ecosystem designed for the digital preservation, analysis, and monitoring of ancient artifacts. It combines AI-driven damage detection, 3D reconstruction, and real-time environment monitoring into a single platform.

## 🚀 Project Overview

The project is divided into four main components:

1. **📱 Mobile Application (`archoai_app`)**: A Flutter-based mobile dashboard for archaeologists to visualize artifact data, monitor environmental conditions, and trigger AI analysis.
2. **🧠 AI & Processing Scripts (`scripts/`)**: Python-based backend tools for:
   - **Damage Detection**: Identifying and measuring cracks on artifacts using DeepCrack models.
   - **3D Reconstruction**: Generating 3D models from images using the Tripo API.
   - **Database Integration**: Synchronizing analysis results with Supabase.
3. **🌡️ IoT Monitoring (`monitoring.cpp`)**: ESP32-based firmware for real-time tracking of temperature, humidity, and air quality (VOC/Gas) in storage areas.
4. **🌐 Landing Page (`landing_page/`)**: A modern web interface providing project information and vision.

---

## 🛠️ Tech Stack

- **Mobile**: Flutter, Dart, Supabase-Flutter SDK
- **Backend/AI**: Python, PyTorch, OpenCV, Tripo API
- **Database**: Supabase (PostgreSQL, Realtime, Storage)
- **Embedded**: C++ (Arduino/ESP32), DHT11, MQ-series Gas Sensors, OLED SSD1306
- **Web**: Vanilla JS, CSS (Modern Aurora Design)

---

## 📂 Repository Structure

```tree
.
├── archoai_app/          # Flutter mobile application
├── landing_page/         # Project landing page
├── scripts/              # AI inference and utility scripts
│   ├── main.py           # Main processing script
│   ├── withbutton.py     # Tripo 3D generation integration
│   └── util/             # AI model utilities and networks
├── monitoring.cpp        # ESP32 IoT Monitoring Firmware
└── README.md             # This file
```

---

## 🔧 Setup & Installation

### AI Scripts
1. Navigate to the `scripts` folder.
2. Create a virtual environment: `python -m venv venv`.
3. Install dependencies: `pip install -r requirements.txt`.
4. Ensure your `pretrained_net_G.pth` model is in the root of the scripts folder.

### Mobile App
1. Ensure Flutter is installed.
2. Run `flutter pub get` inside `archoai_app`.
3. Run the app: `flutter run`.

### IoT Monitoring
1. Compile and flash `monitoring.cpp` using the Arduino IDE or PlatformIO.
2. Ensure you have the `Adafruit_SSD1306`, `DHT`, and `WiFi` libraries installed.
3. Update `WIFI_SSID` and `SUPABASE_KEY` in the source code.

---

## 📊 Database Schema (Supabase)

To enable artifact processing, create the following table:

```sql
create table public.artifacts (
  id uuid not null default gen_random_uuid (),
  name text not null,
  image_path text not null default ''::text,
  model_url text null,
  created_at timestamp with time zone null default now(),
  status text null default 'pending'::text,
  type text null default 'Unknown'::text,
  material text null default 'Unknown'::text,
  era text null default 'Unknown'::text,
  purpose text null default 'Unknown'::text,
  condition text null default 'Stable'::text,
  crack_percentage double precision null default 0.0,
  constraint artifacts_pkey primary key (id)
);
```

---

## 🛡️ License

This project is developed for archaeological research and conservation purposes. All rights reserved.
