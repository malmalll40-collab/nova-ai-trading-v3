"""Fixed research-only strategy hypotheses and signal-vector audit (v5.5.2)."""
from itertools import combinations

VERSION='5.5.2'
NAMES={'rs_hybrid':'상대강도·Hybrid 결합','regime_router':'국면별 선택 v2'}


def select(*, candidates, core, subregime, market_regime, risk, market_gate):
    # Strict intersection, not an OR that quietly removes a gate.
    combined=bool(candidates['rs_required'] and candidates['transition_soft'])
    if not risk or not market_gate or market_regime=='down' or subregime=='down':
        return {'rs_hybrid':combined,'regime_router':False},'risk_veto'
    # Frozen hypothesis; never selects an in-sample winner or uses future returns.
    routes={'strong_up':('core',core), 'mild_up':('rs_hybrid',combined),
            'sideways':('rs_required',candidates['rs_required']),
            'up_transition':('rs_hybrid',combined)}
    route,buy=routes.get(subregime,('unknown',False))
    return {'rs_hybrid':combined,'regime_router':bool(buy)},route


def signal_audit(points):
    """Aligned points only. HOLD agreement is not evidence of useful redundancy."""
    points=list(points)
    if not points:return {'samples':0,'pairs':[],'status':'UNAVAILABLE'}
    keys=sorted(set.intersection(*(set(p) for p in points)))
    if any(p[k] not in ('BUY','HOLD') for p in points for k in keys):
        return {'samples':0,'pairs':[],'status':'UNAVAILABLE'}
    pairs=[]
    for a,b in combinations(keys,2):
        both=only_a=only_b=0
        for p in points:
            x,y=p[a]=='BUY',p[b]=='BUY'
            both+=x and y;only_a+=x and not y;only_b+=y and not x
        union=both+only_a+only_b
        pairs.append({'a':a,'b':b,'both_buy':both,'only_a':only_a,'only_b':only_b,
                      'both_hold':len(points)-union,'buy_union':union,
                      'jaccard_pct':round(100*both/union,2) if union else None,
                      'agreement_pct':round(100*(len(points)-only_a-only_b)/len(points),2),
                      'identical_observed':bool(union and not only_a and not only_b),
                      'near_duplicate':bool(union>=20 and both/union>=.95)})
    pairs.sort(key=lambda p:(not p['identical_observed'],not p['near_duplicate'],-(p['jaccard_pct'] or 0),p['a'],p['b']))
    return {'status':'READY','samples':len(points),'pairs':pairs,'limitations':'동일 표본의 매수 신호 비교. 무거래 쌍 제외. 논리적 동일성이나 독립 미래 성능을 증명하지 않음.'}
