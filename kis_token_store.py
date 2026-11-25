"""Persistent, encrypted KIS OAuth token cache for sleeping Render web servers.

Uses the *existing* DATABASE_URL.  Each cache entry is encrypted with a key
bound to the KIS credentials that are already supplied through Render secrets.
No plaintext KIS token or app secret is stored in PostgreSQL.

Cross-process PostgreSQL advisory transaction locks prevent duplicate KIS token
issuance when several requests/processes wake up at the same time.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import time
from collections.abc import Callable


SCHEMA = """CREATE TABLE IF NOT EXISTS nova_kis_oauth_cache_v1 (
    cache_id TEXT PRIMARY KEY,
    nonce BYTEA NOT NULL,
    encrypted_token BYTEA NOT NULL,
    expires_at BIGINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
)"""

# Refresh five minutes before expiry, preserving the previous KIS behavior.
EXPIRES_BUFFER_SECONDS = 300


def persistent_cache_enabled() -> bool:
    return os.environ.get('DATABASE_URL', '').strip().startswith(('postgres://', 'postgresql://'))


def _encryption_key(appkey: str, appsecret: str) -> bytes:
    # A domain-separated key derived from high-entropy existing credentials.
    # Changing the broker credentials automatically invalidates old entries.
    seed = b'NOVA-KIS-OAUTH-v1\0' + appkey.encode('utf-8') + b'\0' + appsecret.encode('utf-8')
    return hashlib.sha256(seed).digest()


def _advisory_key(cache_id: str) -> int:
    return int.from_bytes(hashlib.sha256(cache_id.encode('ascii')).digest()[:8], 'big', signed=True)


def load_or_issue(cache_id: str, appkey: str, appsecret: str,
                  issue_token: Callable[[], tuple[str, int]]) -> tuple[str, float]:
    """Return persisted token or issue exactly one token under a DB lock.

    A failed configured DB *must not* silently trigger issuance. Otherwise an
    outage on a free server causes another burst of unwanted Kakao alerts.
    The caller may still use its in-process memory cache if that is valid.
    """
    database_url = os.environ.get('DATABASE_URL', '').strip()
    if not database_url.startswith(('postgres://', 'postgresql://')):
        raise RuntimeError('KIS_TOKEN_STORE_NOT_CONFIGURED')

    import psycopg
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    cipher = AESGCM(_encryption_key(appkey, appsecret))
    associated_data = cache_id.encode('ascii')
    try:
        with psycopg.connect(database_url, connect_timeout=8) as conn:
            with conn.cursor() as cur:
                # Lock before CREATE TABLE, including the very first startup.
                cur.execute('SELECT pg_advisory_xact_lock(%s)', (_advisory_key(cache_id),))
                cur.execute(SCHEMA)
                cur.execute(
                    'SELECT nonce, encrypted_token, expires_at '
                    'FROM nova_kis_oauth_cache_v1 WHERE cache_id = %s',
                    (cache_id,),
                )
                row = cur.fetchone()
                if row is not None and int(row[2]) > time.time() + EXPIRES_BUFFER_SECONDS:
                    try:
                        plaintext = cipher.decrypt(bytes(row[0]), bytes(row[1]), associated_data)
                        token = plaintext.decode('utf-8')
                        if token:
                            return token, float(row[2])
                    except (ValueError, UnicodeDecodeError):
                        # Corrupted or unreadable cache entry: refresh normally.
                        pass
                    except Exception as exc:
                        # Cryptography raises InvalidTag, deliberately restricted
                        # to cache-corruption handling rather than logging secrets.
                        if type(exc).__name__ != 'InvalidTag':
                            raise

                token, duration = issue_token()
                # Issue time may be several seconds after DB connection.
                expiry = int(time.time()) + min(max(int(duration), 1), 24 * 3600)
                nonce = secrets.token_bytes(12)
                encrypted = cipher.encrypt(nonce, token.encode('utf-8'), associated_data)
                cur.execute(
                    'INSERT INTO nova_kis_oauth_cache_v1 '
                    '(cache_id, nonce, encrypted_token, expires_at) '
                    'VALUES (%s, %s, %s, %s) '
                    'ON CONFLICT (cache_id) DO UPDATE SET '
                    'nonce = EXCLUDED.nonce, encrypted_token = EXCLUDED.encrypted_token, '
                    'expires_at = EXCLUDED.expires_at, updated_at = CURRENT_TIMESTAMP',
                    (cache_id, nonce, encrypted, expiry),
                )
                return token, float(expiry)
    except psycopg.Error:
        # Fail closed to avoid repeat token issuance on each Render spin-up.
        raise RuntimeError('KIS_TOKEN_DATABASE_UNAVAILABLE') from None
