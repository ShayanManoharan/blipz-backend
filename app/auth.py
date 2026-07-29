# auth.py
# Auth dependencies for FastAPI routes.
# - require_admin_token: protects internal/cron-triggered endpoints
# - get_current_user_id: verifies a Supabase-issued JWT (from anonymous sign-in) and
#   returns the user's UUID. Uses Supabase's JWKS endpoint (ES256) since this project's
#   "sb_publishable_" key format confirms it's on the new key system, which pairs with
#   asymmetric signing keys rather than a legacy shared HS256 secret.

import jwt
from fastapi import Header, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import Depends
from app.config import settings

security = HTTPBearer()

_jwks_client = jwt.PyJWKClient(f"{settings.supabase_url}/auth/v1/.well-known/jwks.json")


def require_admin_token(x_admin_token: str = Header(...)) -> None:
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="Invalid admin token")


def get_current_user_id(creds: HTTPAuthorizationCredentials = Depends(security)) -> str:
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(creds.credentials)
        payload = jwt.decode(
            creds.credentials,
            signing_key.key,
            algorithms=["ES256"],
            audience="authenticated",
        )
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload["sub"]
