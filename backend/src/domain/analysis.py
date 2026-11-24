"""校准证书的报告日快照、车间产线最小停线组合与分发判定。"""

from collections import Counter, defaultdict
from datetime import date

from .spec import ACTIVE, CERTIFICATE, INACTIVE, INSTRUMENT, LINE, PLAN, PLAN_ITEM, SPEC


OVERDUE = '已过期'
MISSING = '缺少证书'
DUE_TODAY = '当日到期'
DUE_SOON = '即将到期'
VALID = '有效'
STATUS_ORDER = (OVERDUE, MISSING, DUE_TODAY, DUE_SOON, VALID)

LINE_OK = '有可用方案'
LINE_PENDING = '待核'
LINE_STOPPED = '已停线'
PLAN_OK = '可用'
PLAN_BLOCKED = '不可用'
PLAN_PENDING = '待核'
LINE_STATUS_ORDER = (LINE_OK, LINE_PENDING, LINE_STOPPED)

COMBO_LIMIT = 60

_FIELDS = {field['key']: field for field in SPEC['fields']}
_COLUMNS = (
    ('asset_id', '器具编号'), ('asset_name', '器具名称'), ('work_area', '作业区'),
    ('owner', '责任班组'), ('status', '证书状态'), ('certificate_id', '证书编号'),
    ('issued_on', '签发日'), ('expires_on', '到期日'), ('days_remaining', '剩余天数'),
    ('action', '班组处置'),
)
_PLAN_COLUMNS = (
    ('line_code', '产线编号'), ('line_name', '产线名称'), ('plan_code', '方案编号'),
    ('plan_name', '方案名称'), ('instruments', '方案器具'), ('state', '方案状态'),
    ('reasons', '说明'),
)
_COMBO_COLUMNS = (
    ('line_code', '产线编号'), ('line_name', '产线名称'), ('line_status', '产线状态'),
    ('combination', '最小停线组合'), ('plans', '堵住的方案'), ('pending', '待核方案'),
)


def _text(row, key):
    value = row.get(key)
    field = _FIELDS[key]
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= field['max_length']:
        raise ValueError(f'{field["label"]}须填写 1 至 {field["max_length"]} 个字符')
    return value.strip()


def _day(value, label):
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            pass
        else:
            if parsed.isoformat() == value:
                return parsed
    raise ValueError(f'{label}须为有效的 YYYY-MM-DD 日期')


def validate_rows(rows):
    instruments = {}
    certificates = defaultdict(dict)
    lines = {}
    plans = {}
    members = set()
    pending_items = []
    for index, row in enumerate(rows, start=2):
        try:
            kind = row.get('record_type')
            if kind == INSTRUMENT:
                asset_id = _text(row, 'asset_id')
                if asset_id in instruments:
                    raise ValueError(f'器具 {asset_id} 的台账重复')
                status = row.get('asset_status')
                if status not in (ACTIVE, INACTIVE):
                    raise ValueError('器具状态须为在用或停用')
                instruments[asset_id] = {
                    'asset_id': asset_id, 'asset_status': status,
                    **{key: _text(row, key) for key in ('asset_name', 'work_area', 'owner')},
                }
            elif kind == CERTIFICATE:
                asset_id = _text(row, 'asset_id')
                code = _text(row, 'certificate_id')
                issued = _day(row.get('issued_on'), '签发日')
                expires = _day(row.get('expires_on'), '到期日')
                if expires < issued:
                    raise ValueError(f'证书 {code} 的到期日早于签发日')
                if code in certificates[asset_id]:
                    raise ValueError(f'器具 {asset_id} 的证书 {code} 重复')
                certificates[asset_id][code] = (issued, expires)
            elif kind == LINE:
                line_code = _text(row, 'line_code')
                if line_code in lines:
                    raise ValueError(f'产线 {line_code} 重复')
                lines[line_code] = {'line_code': line_code, 'line_name': _text(row, 'line_name')}
            elif kind == PLAN:
                plan_code = _text(row, 'plan_code')
                if plan_code in plans:
                    raise ValueError(f'检验方案 {plan_code} 重复')
                plans[plan_code] = {
                    'plan_code': plan_code, 'plan_name': _text(row, 'plan_name'),
                    'line_code': _text(row, 'line_code'), 'items': [],
                }
            elif kind == PLAN_ITEM:
                plan_code = _text(row, 'plan_code')
                asset_id = _text(row, 'asset_id')
                if (plan_code, asset_id) in members:
                    raise ValueError(f'方案 {plan_code} 重复登记器具 {asset_id}')
                members.add((plan_code, asset_id))
                pending_items.append((index, plan_code, asset_id))
            else:
                raise ValueError('记录类型须为器具台账、校准证书、产线、检验方案或方案器具')
        except ValueError as error:
            raise ValueError(f'CSV 第 {index} 行 · {error}') from None

    for index, plan_code, asset_id in pending_items:
        if plan_code not in plans:
            raise ValueError(f'CSV 第 {index} 行 · 方案器具缺少对应的检验方案 {plan_code}')
        plans[plan_code]['items'].append(asset_id)
    for plan in plans.values():
        if plan['line_code'] not in lines:
            raise ValueError(f'检验方案 {plan["plan_code"]} 的产线 {plan["line_code"]} 不在产线清单')
        for asset_id in plan['items']:
            if asset_id not in instruments:
                raise ValueError(f'方案 {plan["plan_code"]} 的器具 {asset_id} 不在器具台账')
    unknown = sorted(certificates.keys() - instruments.keys())
    if unknown:
        raise ValueError(f'证书缺少对应的器具台账，例如 {unknown[0]}')
    return instruments, certificates, lines, plans


def _parameters(params):
    values = {}
    for field in SPEC['params']:
        key = field['key']
        value = params.get(key, field['default'])
        if field['type'] == 'date':
            value = _day(value, field['label'])
        elif type(value) is not int or not field['min'] <= value <= field['max']:
            raise ValueError(f'{field["label"]}须为 {field["min"]} 至 {field["max"]} 的整数')
        values[key] = value
    return values


def _latest_certificate(certificates, report_day):
    return max(
        (code for code, (issued, _) in certificates.items() if issued <= report_day),
        key=lambda code: (certificates[code][0], code), default=None,
    )


def _asset_report(instrument, certificates, report_day, horizon):
    code = _latest_certificate(certificates, report_day)
    owner = instrument['owner']
    issued = expires = remaining = None
    if code is None:
        status = MISSING
        action = f'{owner}核对器具铭牌并向计量室补录证书。确认校准有效前暂停用于检验。'
    else:
        issued, expires = certificates[code]
        remaining = (expires - report_day).days
        if remaining < 0:
            status = OVERDUE
            action = f'{owner}停用并贴隔离标签，换用有效备用器具，登记送校。'
        elif remaining == 0:
            status = DUE_TODAY
            action = f'{owner}本班交接前安排换具和送校，次日启用前核对新证书。'
        elif remaining <= horizon:
            status = DUE_SOON
            action = f'{owner}在 {expires} 前预约送校并落实有效备用器具。'
        else:
            status = VALID
            action = f'{owner}继续使用，按台账日期复核校准计划。'

    return {
        **instrument, 'status': status, 'certificate_id': code,
        'issued_on': issued.isoformat() if issued else None,
        'expires_on': expires.isoformat() if expires else None,
        'days_remaining': remaining, 'action': action,
    }


def _asset_state(instrument, certificates, report_day):
    code = _latest_certificate(certificates, report_day)
    expires = certificates[code][1] if code else None
    if instrument['asset_status'] != ACTIVE:
        state = 'inactive'
    elif code is None:
        state = 'missing'
    elif expires < report_day:
        state = 'expired'
    else:
        state = 'valid'
    return {'state': state, 'certificate_id': code,
            'expires_on': expires.isoformat() if expires else None}


def prepare(rows, params):
    """校验批次并固定本次计算的报告日、证书与方案口径。"""
    options = _parameters(params)
    day = options['report_day']
    horizon = options['horizon_days']
    threshold = options['min_coverage_percent']
    instruments, certificates, lines, plans = validate_rows(rows)
    snapshot = [
        _asset_report(instrument, certificates.get(asset_id, {}), day, horizon)
        for asset_id, instrument in instruments.items() if instrument['asset_status'] == ACTIVE
    ]
    snapshot.sort(key=lambda row: (STATUS_ORDER.index(row['status']), row['days_remaining'] or 0, row['asset_id']))
    states = {asset_id: _asset_state(instrument, certificates.get(asset_id, {}), day)
              for asset_id, instrument in instruments.items()}
    return {
        'day': day, 'horizon': horizon, 'threshold': threshold,
        'instruments': instruments, 'certificates': certificates,
        'lines': lines, 'plans': plans, 'snapshot': snapshot, 'states': states,
        'line_codes': sorted(lines),
    }


def _reason(asset_id, state):
    if state == 'inactive':
        return f'{asset_id} 器具停用'
    if state == 'expired':
        return f'{asset_id} 证书已过期'
    return f'{asset_id} 缺少证书'


def _plan_report(plan, states):
    items = sorted(set(plan['items']))
    reasons = []
    if not items:
        state = PLAN_PENDING
        reasons.append('未登记方案器具')
    else:
        blocked = [_reason(asset_id, states[asset_id]['state'])
                   for asset_id in items if states[asset_id]['state'] in ('inactive', 'expired')]
        pending = [_reason(asset_id, states[asset_id]['state'])
                   for asset_id in items if states[asset_id]['state'] == 'missing']
        if blocked:
            state = PLAN_BLOCKED
            reasons = blocked + pending
        elif pending:
            state = PLAN_PENDING
            reasons = pending
        else:
            state = PLAN_OK
    return {
        'plan_code': plan['plan_code'], 'plan_name': plan['plan_name'],
        'line_code': plan['line_code'], 'instruments': items,
        'state': state, 'reasons': reasons,
    }


def _minimal_sets(groups, limit=COMBO_LIMIT):
    """求全部极小命中集：同时缺少集合内器具才会命中每套可用方案。"""
    frontier = [frozenset()]
    truncated = False
    if not groups:
        return [], False
    for group in sorted(groups, key=lambda item: (len(item), tuple(sorted(item)))):
        if not group:
            continue
        expanded = set()
        for combo in frontier:
            if combo & group:
                expanded.add(combo)
            else:
                for asset_id in sorted(group):
                    expanded.add(combo | {asset_id})
        ordered = sorted(expanded, key=lambda combo: (len(combo), tuple(sorted(combo))))
        kept = []
        for combo in ordered:
            if not any(previous <= combo for previous in kept):
                kept.append(combo)
        if len(kept) > limit:
            kept = kept[:limit]
            truncated = True
        frontier = kept
    ordered = sorted(frontier, key=lambda combo: (len(combo), tuple(sorted(combo))))
    return ordered, truncated


def _plan_ref(plan):
    return {'plan_code': plan['plan_code'], 'plan_name': plan['plan_name']}


def line_report(context, line_code):
    """计算单条产线的方案可用性和极小停线组合，可分批续算。"""
    line = context['lines'][line_code]
    plans = [_plan_report(plan, context['states'])
             for plan in sorted(context['plans'].values(), key=lambda item: item['plan_code'])
             if plan['line_code'] == line_code]
    usable = [plan for plan in plans if plan['state'] == PLAN_OK]
    pending = [plan for plan in plans if plan['state'] == PLAN_PENDING]
    blocked = [plan for plan in plans if plan['state'] == PLAN_BLOCKED]

    combos = []
    truncated = False
    if usable:
        status = LINE_OK
        note = '列出极小停线组合：组合内器具同时缺失时本线没有可用方案，少缺其中任一台仍至少有一套方案可用。'
        groups = [frozenset(plan['instruments']) for plan in usable]
        minimal, truncated = _minimal_sets(groups)
        combos = [{
            'key': '+'.join(sorted(combo)),
            'instruments': sorted(combo),
            'plans': [_plan_ref(plan) for plan in usable if set(plan['instruments']) & combo],
        } for combo in minimal]
    elif pending:
        status = LINE_PENDING
        note = '现有方案缺档，待核对证书或补登方案器具后再确认是否停线。'
    elif plans:
        status = LINE_STOPPED
        note = '已确认无可用检验方案，记为已停线。'
    else:
        status = LINE_PENDING
        note = '未登记检验方案，待核对方案清单。'

    return {
        'line_code': line['line_code'], 'line_name': line['line_name'],
        'status': status, 'note': note,
        'usable_count': len(usable), 'pending_count': len(pending), 'blocked_count': len(blocked),
        'plans': plans, 'combos': combos, 'combos_truncated': truncated,
        'pending_plans': [_plan_ref(plan) for plan in pending],
        'blocked_plans': [_plan_ref(plan) for plan in blocked],
    }


def _normalize_line(line):
    """续算重试时按组合键去重，避免重复列出同一组合。"""
    seen = set()
    combos = []
    for combo in line.get('combos', []):
        key = combo.get('key') or '+'.join(sorted(combo.get('instruments', [])))
        if key in seen:
            continue
        seen.add(key)
        combos.append({**combo, 'key': key, 'instruments': sorted(combo.get('instruments', []))})
    combos.sort(key=lambda combo: (len(combo['instruments']), combo['key']))
    return {**line, 'combos': combos}


def assemble(context, line_reports):
    day = context['day']
    horizon = context['horizon']
    threshold = context['threshold']
    snapshot = context['snapshot']
    counts = Counter(row['status'] for row in snapshot)
    total = len(snapshot)
    covered = total - counts[MISSING]
    coverage = f'{covered / total:.2%}' if total else '待核实'
    ready = total > 0 and covered * 100 >= threshold * total

    if ready:
        headline = f'{day} 班报可分发 · {covered}/{total} 台有证书，{counts[OVERDUE]} 台已过期'
    elif total:
        headline = f'{day} 班报暂停分发 · 已覆盖 {covered}/{total} 台，低于 {threshold}% 门槛'
    else:
        headline = f'{day} 班报暂停分发 · 台账无在用器具'

    lines = [_normalize_line(line_reports[code]) for code in context['line_codes'] if code in line_reports]
    line_counts = Counter(line['status'] for line in lines)
    combo_total = sum(len(line['combos']) for line in lines)
    plan_rows = [{
        'line_code': plan['line_code'], 'line_name': context['lines'][plan['line_code']]['line_name'],
        'plan_code': plan['plan_code'], 'plan_name': plan['plan_name'],
        'instruments': ' + '.join(plan['instruments']) or '—',
        'state': plan['state'], 'reasons': '；'.join(plan['reasons']) or '—',
    } for plan in sorted((_plan_report(plan, context['states']) for plan in context['plans'].values()),
                         key=lambda item: (item['line_code'], item['plan_code']))]
    combo_rows = []
    for line in lines:
        if line['combos']:
            for combo in line['combos']:
                combo_rows.append({
                    'line_code': line['line_code'], 'line_name': line['line_name'],
                    'line_status': line['status'],
                    'combination': ' + '.join(combo['instruments']),
                    'plans': '；'.join(f'{plan["plan_code"]} {plan["plan_name"]}' for plan in combo['plans']) or '—',
                    'pending': '；'.join(f'{plan["plan_code"]} {plan["plan_name"]}' for plan in line['pending_plans']) or '—',
                })
        else:
            combo_rows.append({
                'line_code': line['line_code'], 'line_name': line['line_name'],
                'line_status': line['status'], 'combination': '—',
                'plans': '；'.join(f'{plan["plan_code"]} {plan["plan_name"]}' for plan in line['blocked_plans']) or '—',
                'pending': '；'.join(f'{plan["plan_code"]} {plan["plan_name"]}' for plan in line['pending_plans']) or '—',
            })

    tables = [{
        'title': '在用器具证书与班组处置',
        'columns': [{'key': key, 'label': label} for key, label in _COLUMNS],
        'rows': snapshot,
    }]
    if plan_rows:
        tables.append({
            'title': '检验方案可用性',
            'columns': [{'key': key, 'label': label} for key, label in _PLAN_COLUMNS],
            'rows': plan_rows,
        })
    if lines:
        tables.append({
            'title': '产线最小停线组合',
            'columns': [{'key': key, 'label': label} for key, label in _COMBO_COLUMNS],
            'rows': combo_rows,
        })

    charts = [{
        'title': '证书状态（器具数）', 'kind': 'bar',
        'series': [{'label': status, 'value': counts[status]} for status in STATUS_ORDER],
    }] if total else []
    if lines:
        charts.append({
            'title': '产线状态（产线数）', 'kind': 'bar',
            'series': [{'label': status, 'value': line_counts[status]} for status in LINE_STATUS_ORDER],
        })

    metrics = [
        {'label': '在用器具', 'value': total, 'detail': '按导入台账的在用状态统计'},
        {'label': '证书覆盖率', 'value': coverage, 'detail': f'{covered}/{total} 台 · 门槛 {threshold}%'},
        {'label': '已过期', 'value': counts[OVERDUE], 'detail': '立即停用并换具'},
        {'label': '临期（含当日）', 'value': counts[DUE_SOON] + counts[DUE_TODAY],
         'detail': f'其中 {counts[DUE_TODAY]} 台当日到期'},
        {'label': '缺少证书', 'value': counts[MISSING], 'detail': '截至报告日无已签发证书'},
    ]
    if lines:
        metrics.extend([
            {'label': '已停线产线', 'value': line_counts[LINE_STOPPED], 'detail': '已确认无可用方案'},
            {'label': '待核产线', 'value': line_counts[LINE_PENDING], 'detail': '方案缺档，暂不判定停线'},
            {'label': '最小停线组合', 'value': combo_total, 'detail': '组合内器具同时缺失才停线'},
        ])

    return {
        'ready': ready,
        'headline': headline,
        'note': f'报告日 {day}，临期窗口 {horizon} 天。证书覆盖率 {coverage}，分发门槛 {threshold}%。'
                '已过期证书计入覆盖。同日签发按证书编号取字典序末位。'
                '产线仅统计按报告日证书可用的方案，缺档方案列入待核。',
        'metrics': metrics,
        'tables': tables,
        'charts': charts,
        'lines': lines,
        'progress': {'lines_done': len(lines), 'lines_total': len(context['line_codes'])},
    }


def analyze(rows, params) -> dict:
    context = prepare(rows, params)
    return assemble(context, {code: line_report(context, code) for code in context['line_codes']})
