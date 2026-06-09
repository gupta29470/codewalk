import jwt as _jwt         
import time as _time
import requests as _requests

# In-memory token cache: {(app_id, installation_id): (token, expiry_timestamp)}
_token_cache: dict[tuple[str, str], tuple[str, float]] = {}
_TOKEN_CACHE_MARGIN = 300  # refresh 5 min before expiry

def get_installation_token(app_id: str, private_key_pem: str, installation_id: str) -> str:
    """Generate a short-lived GitHub App installation token (~1 hour, auto-expires).
    Tokens are cached in memory and refreshed 5 minutes before expiry.
    """
    cache_key = (app_id, installation_id)
    cached = _token_cache.get(cache_key)
    if cached:
        token, expiry = cached
        if _time.time() < expiry - _TOKEN_CACHE_MARGIN:
            return token

    # 1. Sign a JWT valid for 60 seconds
    now = int(_time.time())
    payload = {"iat": now - 60, "exp": now + 60, "iss": app_id}
    jwt_token = _jwt.encode(payload, private_key_pem, algorithm="RS256")

    # 2. Exchange JWT for installation token
    resp = _requests.post(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
        },
    )
    resp.raise_for_status()
    data = resp.json()
    token = data["token"]
    # GitHub returns expiry as ISO string; parse it
    expiry_str = data.get("expires_at", "")
    try:
        from datetime import datetime, timezone
        expiry_dt = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
        expiry_ts = expiry_dt.timestamp()
    except Exception:
        expiry_ts = now + 3600  # fallback: 1 hour

    _token_cache[cache_key] = (token, expiry_ts)
    return token

