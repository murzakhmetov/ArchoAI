import requests
import json
import math
import random
from datetime import datetime, timedelta
import os
import sys
from dotenv import dotenv_values, load_dotenv

from supabase_models import resolve_supabase_url

HERE = os.path.dirname(os.path.abspath(__file__))
# Load credentials. The app's .env lives at <repo>/archoai_app/.env. This script sits in
# <repo>/scripts/scripts/, so the first path is the right one; the second one is kept for a
# flat layout where the script is placed directly in <repo>/scripts/.
for env_path in (
    os.path.join(HERE, '..', '..', 'archoai_app', '.env'),
    os.path.join(HERE, '..', 'archoai_app', '.env'),
):
    load_dotenv(env_path)

# The server: SUPABASE_URL (the Raspberry Pi, http://<pi address>:54321) or a cloud SUPABASE_PROJECT_REF.
SUPABASE_URL = resolve_supabase_url()
# This script writes created_at and 144 rows at once. The publishable (anon) key may only insert the
# three sensor columns, one row per 2 seconds, so it needs a secret key. Pass it through the SHELL
# environment (SUPABASE_SERVICE_KEY), never through archoai_app/.env: that file is bundled into the app.
# On the Raspberry Pi server the secret key is read from supabase/.env (ARCHOAI_SECRET_KEY, git-ignored).
SERVER_SECRET_KEY = dotenv_values(os.path.join(HERE, '..', '..', 'supabase', '.env')).get("ARCHOAI_SECRET_KEY")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or SERVER_SECRET_KEY or os.getenv("SUPABASE_KEY")
DB_URL = f"{SUPABASE_URL}/rest/v1/sensor_data"

headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
}

def generate_beautiful_data(num_points=144): # 24 hours with 10 min steps
    """Insert the generated points. Returns True on success, False if nothing was inserted."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: SUPABASE_URL and a key must be set: SUPABASE_SERVICE_KEY in your shell (this script needs a secret key), or SUPABASE_KEY.")
        return False

    now = datetime.utcnow()
    data_points = []
    
    print(f"Generating {num_points} smooth data points...")
    
    for i in range(num_points):
        # Time calculation: going back in time
        time_point = now - timedelta(minutes=10 * (num_points - i))
        
        # Sine wave for Temperature (cycles over 24h)
        # i / num_points goes from 0 to 1 over 24h
        # 2 * pi * (i / num_points) is one full cycle
        # We'll adjust it so peak is at "mid-day"
        angle = 2 * math.pi * (i / num_points) - (math.pi / 2) # Start at low, peak at middle
        
        # Temp: base 25, amplitud 5 -> 20 to 30 range
        temp = 25 + 5 * math.sin(angle) + random.uniform(-0.2, 0.2)
        
        # Hum: inversely related to temp, base 50, amplitude 15 -> 35 to 65 range
        hum = 50 - 15 * math.sin(angle) + random.uniform(-0.5, 0.5)
        
        # Air quality: steady with minor fluctuations, maybe a slight peak at "morning/evening"
        air = 150 + 20 * math.sin(2 * angle) + random.uniform(-5, 5)
        
        # Soil (migration 20261128000002): the soil warms later and less than the air, and slowly dries.
        soil_temp = 20 + 2.5 * math.sin(angle - 0.8) + random.uniform(-0.1, 0.1)
        soil_moist = 45 - 8 * (i / num_points) + 2 * math.sin(angle) + random.uniform(-0.5, 0.5)
        # Air quality in ppm (CO2 equivalent), outdoor air is about 420 ppm.
        ppm = 480 + 60 * math.sin(2 * angle) + random.uniform(-10, 10)

        payload = {
            "temperature": round(temp, 1),
            "humidity": round(hum, 1),
            "air_quality": round(air, 1),
            "soil_moisture": round(soil_moist, 1),
            "soil_temperature": round(soil_temp, 2),
            "air_ppm": round(ppm),
            "created_at": time_point.isoformat() + "Z"
        }
        data_points.append(payload)

    # Bulk insert
    try:
        response = requests.post(DB_URL, headers=headers, json=data_points, timeout=60)
        if response.status_code in [200, 201]:
            print(f"Successfully inserted {num_points} smooth records.")
            return True
        else:
            print(f"Error: {response.status_code}")
            print(response.text)
            if response.status_code in (401, 403) and not (os.getenv("SUPABASE_SERVICE_KEY") or SERVER_SECRET_KEY):
                print("Hint: the publishable key cannot write created_at or bulk rows. Set SUPABASE_SERVICE_KEY "
                      "in your shell (a secret key, not in archoai_app/.env) and run the script again.")
                return False
            # If created_at fails, try without it
            if "created_at" in response.text:
                print("Table might not have 'created_at' column. Retrying without it...")
                for p in data_points: del p["created_at"]
                retry_response = requests.post(DB_URL, headers=headers, json=data_points, timeout=60)
                if retry_response.status_code in [200, 201]:
                    print(f"Successfully inserted {num_points} smooth records (without created_at).")
                    return True
                print(f"Retry failed: {retry_response.status_code}")
                print(retry_response.text)
    except Exception as e:
        print(f"An error occurred: {e}")
    return False

if __name__ == "__main__":
    # Exit with a non-zero status when nothing was inserted, so scripts and CI notice the failure
    sys.exit(0 if generate_beautiful_data(144) else 1)
