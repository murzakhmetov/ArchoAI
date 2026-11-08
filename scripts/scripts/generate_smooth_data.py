import requests
import json
import math
import random
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

# Load credentials
env_path = os.path.join(os.path.dirname(__file__), '..', 'archoai_app', '.env')
load_dotenv(env_path)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
DB_URL = f"{SUPABASE_URL}/rest/v1/sensor_data"

headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
}

def generate_beautiful_data(num_points=144): # 24 hours with 10 min steps
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
        
        payload = {
            "temperature": round(temp, 1),
            "humidity": round(hum, 1),
            "air_quality": round(air, 1),
            "created_at": time_point.isoformat() + "Z"
        }
        data_points.append(payload)

    # Bulk insert
    try:
        response = requests.post(DB_URL, headers=headers, json=data_points)
        if response.status_code in [200, 201]:
            print(f"Successfully inserted {num_points} smooth records.")
        else:
            print(f"Error: {response.status_code}")
            print(response.text)
            # If created_at fails, try without it
            if "created_at" in response.text:
                print("Table might not have 'created_at' column. Retrying without it...")
                for p in data_points: del p["created_at"]
                requests.post(DB_URL, headers=headers, json=data_points)
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    generate_beautiful_data(144)
