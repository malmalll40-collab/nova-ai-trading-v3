"""GitHub Actions short-lived identity; no stored scheduler secret."""
import hashlib
import re
import threading
import time
import jwt

ISSUER='https://token.actions.githubusercontent.com'
AUDIENCE='https://nova-ai-trading-v3-mobile.onrender.com/api/research/forward/scheduled'
REPO='malmalll40-collab/nova-ai-trading-v3'
WORKFLOW=REPO+'/.github/workflows/forward-shadow-schedule.yml@refs/heads/main'
EXPECTED={'repository':REPO,'repository_id':'1450967919','repository_owner_id':'349713062',
          'ref':'refs/heads/main','workflow_ref':WORKFLOW,'runner_environment':'github-hosted'}
REQUIRED=['exp','iat','nbf','iss','aud','sub','jti',*EXPECTED,'event_name']
client=jwt.PyJWKClient(ISSUER+'/.well-known/jwks',cache_jwk_set=True,lifespan=300,timeout=5)
lock=threading.Lock()

def identity(c):
    return (all(str(c.get(k))==v for k,v in EXPECTED.items())
            and c.get('event_name') in ('schedule','workflow_dispatch')
            and c.get('iss')==ISSUER and c.get('aud')==AUDIENCE
            and isinstance(c.get('jti'),str) and 1<=len(c['jti'])<=200)

def authenticate(token,*,keys=client,clock=time.time):
    if not isinstance(token,str) or not 1<=len(token)<=8192:raise ValueError('IDENTITY_INVALID')
    try:
        h=jwt.get_unverified_header(token)
        if h.get('alg')!='RS256' or not isinstance(h.get('kid'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',h['kid']):
            raise ValueError('IDENTITY_INVALID')
        preview=jwt.decode(token,options={'verify_signature':False})
        if not identity(preview):raise ValueError('IDENTITY_INVALID')
        # Select only a key from the fixed issuer's cached set; never fetch an
        # attacker-specified URL or force a refresh for an unknown kid.
        with lock:
            matches=[k for k in keys.get_signing_keys() if k.key_id==h['kid']]
        if len(matches)!=1:raise ValueError('IDENTITY_KEY_UNAVAILABLE')
        c=jwt.decode(token,matches[0].key,algorithms=['RS256'],audience=AUDIENCE,issuer=ISSUER,
                     options={'require':REQUIRED,'strict_aud':True},leeway=15)
        if not identity(c) or not 0<=c['exp']-c['iat']<=600 or not -15<=clock()-c['iat']<=600:
            raise ValueError('IDENTITY_INVALID')
        return hashlib.sha256(c['jti'].encode()).hexdigest()
    except Exception:
        raise ValueError('IDENTITY_INVALID') from None
