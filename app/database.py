# database.py
# handles the setup and connection to the Supabase database
# all other files import 'supabase" from here to interact with the database

import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file
Supabase_URL = os.getenv("SUPABASE_URL")
Supabase_Key = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(Supabase_URL, Supabase_Key)
