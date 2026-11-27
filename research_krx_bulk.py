"""Fixed NOVA research cohort, KRX Open API native quotations. No adjustment or imputation."""
import csv
import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from datetime import date
from functools import lru_cache

from nova_v574_data_audit import session_dates
from nova_v576_expanded_coverage import corrected_sessions
from research_history_checkpoint import canonical, digest
from research_history_status import START, END
from research_krx_snapshot import BASIS, INDEX_NAMES, MARKETS, SERVICES, STOCK_FIELDS, _numeric, manifest

CODES = {symbol[:6]: symbol for market in MARKETS for symbol in SERVICES[market]}
INDEX_FIELDS = {'open': 'OPNPRC_IDX', 'high': 'HGPRC_IDX', 'low': 'LWPRC_IDX',
                'close': 'CLSPRC_IDX', 'volume': 'ACC_TRDVOL', 'turnover': 'ACC_TRDVAL'}
FIELDS = ('open', 'high', 'low', 'close', 'volume', 'turnover')
CSV_COLUMNS = ('day', 'symbol', 'code', 'name', 'market', 'service', *FIELDS, 'quote_status')
SYMBOLS = frozenset(CODES.values()) | {SERVICES[k][0] for k in INDEX_NAMES}


@lru_cache(maxsize=1)
def scope():
    days, _ = corrected_sessions(session_dates(START, END), START, END)
    return manifest(days)[0], days


class SourceError(ValueError):
    """Safe operator diagnostics: fixed code plus requested date/service/symbol."""
    def __init__(self, code, day=None, service=None, symbol=None):
        super().__init__(code)
        self.details = {k: v for k, v in (
            ('reason', code), ('day', day.isoformat() if day else None),
            ('service', service), ('symbol', symbol)) if v is not None}


def identity(service, row):
    if service in INDEX_NAMES:
        return SERVICES[service][0] if row.get('IDX_NM') in INDEX_NAMES[service] else None
    code = str(row.get('ISU_CD', '')).strip()
    if re.fullmatch(r'KR7[0-9]{6}[0-9A-Z]{3}', code):
        code = code[3:9]
    return CODES.get(code)


def select_rows(service, day, payload):
    rows = payload.get('OutBlock_1') if isinstance(payload, dict) else None
    if service not in SERVICES or not isinstance(rows, list) or not 0 < len(rows) <= 10000:
        raise SourceError('KRX_INVALID_RESPONSE', day, service)
    if str(payload.get('respCode', '0')) not in {'0', '00', '000', '200'}:
        raise SourceError('KRX_INVALID_RESPONSE', day, service)
    selected = []
    stamp = day.strftime('%Y%m%d')
    for row in rows:
        if not isinstance(row, dict) or row.get('BAS_DD') != stamp:
            raise SourceError('KRX_DATE_MISMATCH', day, service)
        symbol = identity(service, row)
        if symbol is not None:
            selected.append([service, row])
    return selected


def normalize(service, row, day):
    if service == 'KRX_existing_stock':
        native = row.get('market_service')
        symbol = row.get('symbol')
        values = row.get('values')
        if (native not in MARKETS or symbol not in SERVICES[native] or not isinstance(values,dict)
                or set(values) != set(FIELDS) or row.get('day') != day.isoformat()):
            raise SourceError('KRX_LEGACY_VALUE_CONFLICT', day)
        mapped = {'ISU_CD': symbol[:6], 'MKT_NM': MARKETS[native], 'BAS_DD': day.strftime('%Y%m%d')}
        mapped.update({v: values[k] for k,v in STOCK_FIELDS.items()})
        return {**normalize(native, mapped, day), 'service': service}
    if service.startswith('FSC_'):
        from research_krx_period import INDEX, STOCK, INDEX_SYMBOLS, MARKET_FIELDS
        if service not in (INDEX, STOCK) or not isinstance(row, dict):
            raise SourceError('FSC_INVALID_ROW', day)
        if any(k not in row for k in MARKET_FIELDS.values()):
            raise SourceError('FSC_MISSING_FIELD', day, service)
        if service == INDEX:
            code = INDEX_SYMBOLS.get(row.get('idxNm'))
            native = {'^KS11': 'kospi_dd_trd', '^KQ11': 'kosdaq_dd_trd'}.get(code)
            mapped = {'IDX_NM': row.get('idxNm')}
            fields = INDEX_FIELDS
        else:
            native = {'KOSPI':'stk_bydd_trd','KOSDAQ':'ksq_bydd_trd'}.get(row.get('mrktCtg'))
            mapped = {'ISU_CD':row.get('srtnCd'), 'ISU_NM':row.get('itmsNm'), 'MKT_NM':row.get('mrktCtg')}
            fields = STOCK_FIELDS
        if not native:
            raise SourceError('FSC_MARKET_OR_INDEX_MISMATCH', day, service)
        mapped['BAS_DD'] = row.get('basDt')
        mapped.update({fields[k]: row[field] for k,field in MARKET_FIELDS.items()})
        bar = normalize(native, mapped, day)
        return {**bar, 'service': service}
    if service not in SERVICES or not isinstance(row, dict) or row.get('BAS_DD') != day.strftime('%Y%m%d'):
        raise SourceError('KRX_DATE_OR_SERVICE_MISMATCH', day)
    stock = service in MARKETS
    symbol = identity(service, row)
    if symbol is None:
        raise SourceError('KRX_UNEXPECTED_SYMBOL', day, service)
    market = MARKETS[service] if stock else ('KOSPI' if service == 'kospi_dd_trd' else 'KOSDAQ')
    if stock and row.get('MKT_NM') != market:
        raise SourceError('KRX_MARKET_MISMATCH', day, service, symbol)
    mapping = STOCK_FIELDS if stock else INDEX_FIELDS
    if any(f not in row for f in mapping.values()):
        raise SourceError('KRX_MISSING_FIELD', day, service, symbol)
    values = {}
    for key, field in mapping.items():
        value = row[field]
        if stock and value in ('-', '', None):
            values[key] = None
            continue
        try:
            values[key] = _numeric(value, integer=stock or key in ('volume', 'turnover'),
                                   positive=False)
        except ValueError:
            raise SourceError('KRX_INVALID_' + key.upper(), day, service, symbol) from None
    o, h, l, c, v, t = (values[k] for k in FIELDS)
    status = 'TRADED'
    if stock and v in (0, None) and t in (0, None) and all(x in (0, None) for x in (o, h, l)):
        # The untouched provider row preserves the distinction between literal zero and a dash.
        # The normalized prices represent unavailable OHLC on a date without a quoted range.
        status = 'NO_TRADES' if v == 0 and t == 0 else 'NO_QUOTE_REPORTED'
        values.update(open=None, high=None, low=None, close=c or None)
    else:
        if any(x is None or x <= 0 for x in (o, h, l, c)) or h < max(o, l, c) or l > min(o, c):
            raise SourceError('KRX_INVALID_OHLC', day, service, symbol)
        if v is None or t is None or ((v == 0) != (t == 0)):
            raise SourceError('KRX_INVALID_ACTIVITY', day, service, symbol)
        if v == 0:
            status = 'ZERO_VOLUME'
    return {'day': day.isoformat(), 'symbol': symbol, 'code': symbol[:6] if stock else symbol,
            'name': row.get('ISU_NM', '') if stock else row['IDX_NM'], 'market': market,
            'service': service, **values, 'quote_status': status}


def validate_day(day, selected):
    if not isinstance(selected, list) or len(selected) > len(SYMBOLS) * 2:
        raise SourceError('KRX_INVALID_COHORT', day)
    records = {}
    for service, raw in selected:
        bar = normalize(service, raw, day)
        symbol = bar['symbol']
        if symbol in records:
            raise SourceError('KRX_DUPLICATE_SYMBOL', day, service, symbol)
        records[symbol] = (bar, raw)
    if set(records) != SYMBOLS:
        missing = sorted(SYMBOLS - set(records))
        raise SourceError('KRX_COHORT_INCOMPLETE', day, symbol=','.join(missing))
    return records


def archive(entries, days, checksum, overlap):
    """Create private download from the committed bars read back from PostgreSQL."""
    by_symbol = {symbol: [] for symbol in sorted(SYMBOLS)}
    original = io.StringIO()
    statuses = Counter()
    sources = Counter()
    signature = hashlib.sha256()
    for stamp, code, service, body in entries:
        day = date.fromisoformat(stamp)
        raw = json.loads(body)
        row = normalize(service, raw, day)
        if row['symbol'] != code:
            raise ValueError('KRX_RELEASE_IDENTITY_CONFLICT')
        signature.update((canonical([stamp, code, service, raw]) + '\n').encode())
        original.write(json.dumps({'service': service, 'row': raw}, ensure_ascii=False,
                                  separators=(',', ':')) + '\n')
        by_symbol[code].append(row)
        statuses[row['quote_status']] += 1
        sources[service] += 1
    expected_dates = [d.isoformat() for d in days]
    if signature.hexdigest() != checksum or any([row['day'] for row in rows] != expected_dates for rows in by_symbol.values()):
        raise ValueError('KRX_RELEASE_CHECKSUM_OR_COVERAGE_CONFLICT')
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
        hashes = {}
        def add(name, data):
            encoded = data.encode('utf-8')
            z.writestr(name, encoded)
            hashes[name] = hashlib.sha256(encoded).hexdigest()
        for symbol, rows in by_symbol.items():
            buf = io.StringIO(newline='')
            writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
            add('csv/' + symbol.replace('^KS11', 'KOSPI').replace('^KQ11', 'KOSDAQ') + '.csv',
                '\ufeff' + buf.getvalue())
        add('krx_source_rows.jsonl', original.getvalue())
        add('validation.json', json.dumps({
            'origin': 'Korea Exchange (KRX)', 'rows_per_source_service': sources, 'basis': BASIS,
            'source_definitions': {'FSC_getStockPriceInfo_V2': 'Financial Services Commission via data.go.kr; KRX-supplied stocks',
                                   'FSC_getStockMarketIndex_V2': 'Financial Services Commission via data.go.kr; KRX-supplied indices',
                                   'KRX_existing_stock': 'Previously verified KRX Open API stock fields from NOVA (not a full vendor response)',
                                   'KRX native': 'Other service codes denote direct KRX Open API responses'},
            'requested_start': START.isoformat(), 'requested_end': END.isoformat(),
            'first_session': expected_dates[0], 'last_session': expected_dates[-1],
            'sessions': len(days), 'rows': sum(statuses.values()), 'rows_per_symbol': len(days),
            'stock_count': len(CODES), 'index_count': len(INDEX_NAMES), 'quote_status': statuses,
            'legacy_rows_compared': overlap, 'database_sha256': checksum,
            'missing_symbol_dates': 0, 'duplicates': 0, 'files_sha256': dict(hashes),
            'source_rows': 'Original selected provider rows, or the previously stored fields when the service is KRX_existing_stock. Source: Korea Exchange.',
        }, ensure_ascii=False, indent=2) + '\n')
    return output.getvalue()
