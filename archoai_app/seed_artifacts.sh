#!/bin/bash
# Seed artifacts table via Supabase REST API using service_role key
# This bypasses RLS policies

SUPABASE_URL="https://wvhdhfddtusppjmsgmvn.supabase.co"
# We need the full service_role JWT key. The short key doesn't work for REST API auth.
# Let's add an anon INSERT policy first, then insert data.

# Step 1: Use the SQL endpoint via the Supabase Management API to add anon policy
# Step 2: Insert data with anon key

# For now, let's try inserting via the anon key after we fix the RLS
ANON_KEY="sb_publishable_rHDFfoFz_v9zDNKk9o64Gw_Pc-eZvln"

curl -s -X POST "${SUPABASE_URL}/rest/v1/artifacts" \
  -H "apikey: ${ANON_KEY}" \
  -H "Authorization: Bearer ${ANON_KEY}" \
  -H "Content-Type: application/json" \
  -H "Prefer: return=representation" \
  -d '[
    {"name":"Vessel-17","image_path":"","type":"Ceramics","material":"Clay","era":"VI BC","purpose":"Storage","condition":"Good","crack_percentage":2.3,"status":"cataloged"},
    {"name":"Bronze Spear","image_path":"","type":"Weapon","material":"Bronze","era":"VIII BC","purpose":"Combat","condition":"Fair","crack_percentage":12.7,"status":"cataloged"},
    {"name":"Clay Tablet","image_path":"","type":"Tablet","material":"Raw Clay","era":"III mil BC","purpose":"Record","condition":"Critical","crack_percentage":34.5,"status":"restoration"},
    {"name":"Gold Fibula","image_path":"","type":"Jewelry","material":"Gold","era":"V AD","purpose":"Clasp","condition":"Excellent","crack_percentage":0.0,"status":"cataloged"}
  ]'
