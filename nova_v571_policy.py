"""Research-only causal policy challenger. Never changes live signals or places orders."""
from collections import Counter, defaultdict
from math import isfinite
from statistics import mean

VERSION = '5.7.1'
ROUTES = {'strong_up': 'rs_hybrid', 'mild_up': 'rs_hybrid',
          'up_transition': 'transition_soft', 'sideways': 'rs_required'}
LOOKBACK = 120
PRIOR_WEIGHT = 20
MIN_TRADES, MIN_DATES, MIN_SYMBOLS = 15, 10, 5


def valid(r, cost):
    v = r.get('net_pct')
    return (r.get('cost_bps') == cost and type(v) in (int, float)
            and isfinite(v) and v > -100 and r['day'] < r['exit_day'])


def eligible(r):
    return (not r.get('risk_veto') and r.get('market_regime') in ('up', 'range')
            and r.get('subregime') in ROUTES and r.get('signals', {}).get('core') == 'BUY')


def fit(history, day, cost):
    from nova_v570_accuracy import disjoint, metrics
    past = [r for r in history if valid(r, cost) and r['exit_day'] < day and eligible(r)]
    dates = set(sorted({r['day'] for r in past})[-LOOKBACK:])
    past = [r for r in past if r['day'] in dates]
    base = disjoint(past)
    if not base: return {}
    prior = (sum(r['net_pct'] > 0 for r in base) + 2) / (len(base) + 4)
    model = {}
    for regime, route in ROUTES.items():
        group = [r for r in past if r['subregime'] == regime]
        for key, selected in [('core', group), (route, [r for r in group if r['signals'].get(route) == 'BUY'])]:
            sample = disjoint(selected)
            m = metrics(sample, cost)
            n = m['trades']
            if n < MIN_TRADES or m['dates'] < MIN_DATES or m['symbols'] < MIN_SYMBOLS: continue
            model[(regime, key)] = {**m, 'probability': (sum(r['net_pct'] > 0 for r in sample) + PRIOR_WEIGHT * prior) / (n + PRIOR_WEIGHT),
                                    'naive_probability': prior}
    return model


def decide(row, model):
    out = {'version': VERSION, 'signal': 'HOLD', 'state': 'FILTERED', 'route': None,
           'probability': None, 'naive_probability': None, 'research_only': True}
    if not eligible(row): return out
    reg = row['subregime']; route = ROUTES[reg]
    core = model.get((reg, 'core')); alt = model.get((reg, route))
    if core is None: return {**out, 'state': 'INSUFFICIENT_POOL'}
    # Forecast all eligible core opportunities with sufficient evidence, not only BUYs.
    out.update(probability=core['probability'], naive_probability=core['naive_probability'])
    if core['stress_mean_pct'] <= 0: return {**out, 'state': 'NONPOSITIVE_CORE'}
    # A narrower route must improve stressed return and not worsen downside vs core.
    superior = (alt is not None and alt['stress_mean_pct'] > core['stress_mean_pct']
                and alt['tail10_pct'] >= core['tail10_pct'])
    if superior:
        if row['signals'].get(route) != 'BUY': return {**out, 'state': 'ROUTE_FILTER'}
        return {**out, 'signal': 'BUY', 'state': 'SELECTED_ROUTE', 'route': route,
                'probability': alt['probability']}
    # No evidence of a better route: keep core in the research challenger.
    return {**out, 'signal': 'BUY', 'state': 'CORE_FALLBACK', 'route': 'core'}


def historical(frames, symbols, horizon, cost):
    from nova_v570_accuracy import metrics, REGIMES, MARKETS, fit as fit570, decide as decide570
    if not frames or len(frames) != len(symbols) or len(set(symbols)) != len(symbols): raise ValueError('invalid panel')
    if type(horizon) is not int or horizon < 1: raise ValueError('invalid horizon')
    if any(dates != frames[0][0] or len(pts) != len(frames[0][2]) or len(dates) != len(pts) + horizon for dates, _, pts in frames): raise ValueError('unaligned panel')
    if any(a >= b for a, b in zip(frames[0][0], frames[0][0][1:])): raise ValueError('unordered dates')
    history = []; rows = []
    for i, day in enumerate(frames[0][0][:len(frames[0][2])]):
        model = fit(history, day, cost); previous = fit570(history, day, cost); pending = []
        for (dates, _, pts), symbol in zip(frames, symbols):
            p = pts[i]
            r = {'day': dates[i], 'exit_day': dates[i+horizon], 'symbol': symbol,
                 'subregime': p.get('subregime'), 'market_regime': p.get('market_regime'),
                 'risk_veto': p.get('route') == 'risk_veto', 'signals': {**p['signals'], **p['candidates']},
                 'net_pct': 100 * float(p['net']), 'cost_bps': cost}
            if not valid(r, cost): raise ValueError('invalid replay outcome')
            r['decision'] = decide(r, model); r['v570'] = decide570(r, previous); pending.append(r)
        history.extend(pending); rows.extend(pending)
    def compare(data):
        base = [r for r in data if r['signals'].get('core') == 'BUY']
        new = [r for r in data if r['decision']['signal'] == 'BUY']
        previous = [r for r in data if r['v570']['signal'] == 'BUY']
        blocked = [r for r in base if r['decision']['signal'] != 'BUY']
        scored = [r for r in data if r['decision']['probability'] is not None]
        def brier(k): return round(mean((r['decision'][k] - (r['net_pct'] > 0)) ** 2 for r in scored), 6) if scored else None
        matched = [r for r in scored if r['v570']['probability'] is not None]
        def common(model, key): return round(mean((r[model][key] - (r['net_pct'] > 0)) ** 2 for r in matched), 6) if matched else None
        return {'opportunities': len(data), 'core_candidates': len(base), 'eligible_candidates': sum(eligible(r) for r in data), 'core': metrics(base, cost), 'v570': metrics(previous, cost), 'v571': metrics(new, cost),
                'matched_calibration': {'scored': len(matched), 'v570': common('v570','probability'), 'v571': common('decision','probability'), 'v570_naive': common('v570','naive_probability'), 'v571_naive': common('decision','naive_probability')},
                'blocked_wins': sum(r['net_pct'] > 0 for r in blocked), 'blocked_nonpositive': sum(r['net_pct'] <= 0 for r in blocked),
                'scored': len(scored), 'brier': brier('probability'), 'naive_brier': brier('naive_probability'),
                'states': dict(Counter(r['decision']['state'] for r in data))}
    return {'version': VERSION, 'status': 'READY', 'basis': 'HISTORICAL_CAUSAL_REPLAY',
            'strategy_adoption': 'HOLD', 'research_only': True, 'independent_future_verified': False,
            'overall': compare(rows), 'by_regime': {g: compare([r for r in rows if r['subregime'] == g]) for g in REGIMES},
            'by_market': {g: compare([r for r in rows if r['market_regime'] == g]) for g in MARKETS},
            'market_coverage': dict(Counter(r['market_regime'] or 'unknown' for r in rows)),
            'limitations': 'Post-hoc hypothesis; opportunity results, not shared-capital P&L. No live adoption.'}
