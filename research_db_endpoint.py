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


def direct_candidate(dsn):
    """Derive only the documented same-Neon-endpoint hostname variant.

    Preserve credentials in-process, require explicit TLS, reject ambiguous
    libpq routing options; do not mutate environment or disclose the URL.
    """
    try:
        from urllib.parse import urlsplit,urlunsplit,parse_qsl
        from psycopg.conninfo import conninfo_to_dict
        kind=endpoint_kind(dsn)
        if kind not in ('NEON_TRANSACTION_POOLER','NEON_DIRECT_ENDPOINT_HINT'):
            return None
        u=urlsplit(dsn)
        p=conninfo_to_dict(dsn)
        allowed={'host','user','password','dbname','port','sslmode','channel_binding'}
        if set(p)-allowed or u.fragment or not all(p.get(k) for k in ('user','password','dbname')):
            return None
        if p.get('sslmode') not in ('require','verify-ca','verify-full'):
            return None
        if p.get('port','5432')!='5432' or u.hostname!=p['host'].lower():
            return None
        pairs=parse_qsl(u.query,keep_blank_values=True,strict_parsing=True)
        if len({k for k,v in pairs})!=len(pairs) or any(k not in ('sslmode','channel_binding') for k,v in pairs):
            return None
        auth,sep,hostpart=u.netloc.rpartition('@')
        if not sep or not auth or not hostpart:
            return None
        host=p['host'].lower()
        if kind=='NEON_TRANSACTION_POOLER':
            first,rest=host.split('.',1)
            host=first[:-len('-pooler')]+'.'+rest
        direct=urlunsplit((u.scheme,auth+'@'+host+(':5432' if u.port else ''),u.path,u.query,''))
        expected={**p,'host':host}
        if endpoint_kind(direct)!='NEON_DIRECT_ENDPOINT_HINT' or conninfo_to_dict(direct)!=expected:
            return None
        return direct
    except Exception:
        return None
