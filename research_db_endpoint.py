"""Sanitized endpoint classification; never reveals a DSN or grants admission."""
import re

def endpoint_kind(dsn):
    # Official Neon docs: https://neon.com/docs/connect/connection-pooling
    # Transaction poolers are incompatible with session-level advisory locks.
    # A direct-looking hostname is evidence of configuration, not runtime proof.
    try:
        from psycopg.conninfo import conninfo_to_dict
        if not isinstance(dsn,str) or not dsn.startswith(('postgresql://','postgres://')):
            return 'UNKNOWN'
        p=conninfo_to_dict(dsn)
        host=p.get('host','').lower().rstrip('.')
        if p.get('hostaddr') or p.get('service') or ',' in host:
            return 'UNKNOWN'
        if not re.fullmatch(r'ep-[a-z0-9-]+(?:\.[a-z0-9-]+)+\.neon\.tech',host):
            return 'UNKNOWN'
        if host.split('.')[0].endswith('-pooler'):
            return 'NEON_TRANSACTION_POOLER'
        return 'NEON_DIRECT_ENDPOINT_HINT'
    except Exception:
        return 'UNKNOWN'
