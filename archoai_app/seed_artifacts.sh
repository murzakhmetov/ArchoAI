#!/bin/bash
# Seed the artifacts table via the Supabase REST API.
#
# Credentials are NOT stored in this file. Pass them through the environment:
#   SUPABASE_URL       project URL, e.g. https://<project-ref>.supabase.co
#   SUPABASE_ANON_KEY  the project's anon / publishable key
#
# Usage:
#   SUPABASE_URL=https://<project-ref>.supabase.co SUPABASE_ANON_KEY=<key> ./seed_artifacts.sh
#
# The anon key is subject to RLS, so the artifacts table needs an INSERT policy for the anon role
# (add it in the Supabase SQL editor first), otherwise the request is rejected.
# A service_role key would bypass RLS, but it must be kept out of this file and out of git too.

: "${SUPABASE_URL:?Set SUPABASE_URL (e.g. https://<project-ref>.supabase.co)}"
: "${SUPABASE_ANON_KEY:?Set SUPABASE_ANON_KEY (the anon/publishable key of your Supabase project)}"

curl -s -X POST "${SUPABASE_URL%/}/rest/v1/artifacts" \
  -H "apikey: ${SUPABASE_ANON_KEY}" \
  -H "Authorization: Bearer ${SUPABASE_ANON_KEY}" \
  -H "Content-Type: application/json" \
  -H "Prefer: return=representation" \
  -d '[
    {"name":"Vessel-17","image_path":"","type":"Ceramics","material":"Clay","era":"VI BC","purpose":"Storage","condition":"Good","crack_percentage":2.3,"status":"cataloged"},
    {"name":"Bronze Spear","image_path":"","type":"Weapon","material":"Bronze","era":"VIII BC","purpose":"Combat","condition":"Fair","crack_percentage":12.7,"status":"cataloged"},
    {"name":"Clay Tablet","image_path":"","type":"Tablet","material":"Raw Clay","era":"III mil BC","purpose":"Record","condition":"Critical","crack_percentage":34.5,"status":"restoration"},
    {"name":"Gold Fibula","image_path":"","type":"Jewelry","material":"Gold","era":"V AD","purpose":"Clasp","condition":"Excellent","crack_percentage":0.0,"status":"cataloged"}
  ]'
