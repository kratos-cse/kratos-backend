"""Service-role Supabase client. Bypasses RLS entirely — every route in this
module is the trusted backend, so every write goes through this one client."""
from supabase import Client, create_client

from .env import env

_client: Client | None = None


def get_db() -> Client:
    global _client
    if _client is None:
        _client = create_client(env("SUPABASE_URL"), env("SUPABASE_SERVICE_ROLE_KEY"))
    return _client
