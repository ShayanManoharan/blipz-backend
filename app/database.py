# database.py
# handles the setup and connection to the Supabase database
# all other files import 'supabase" from here to interact with the database
# Uses the secret key (server-side, bypasses RLS) — never the publishable key.

from supabase import create_client, Client
from app.config import settings

supabase: Client = create_client(settings.supabase_url, settings.supabase_secret_key)
