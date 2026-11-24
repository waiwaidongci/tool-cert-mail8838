import unittest

from src.domain.analysis import (
    LINE_OK, LINE_PENDING, LINE_STOPPED, PLAN_BLOCKED, PLAN_OK, PLAN_PENDING,
    _minimal_sets, analyze, assemble, line_report, prepare,
)
from src.domain.demo import demo_rows
from src.domain.spec import ACTIVE, CERTIFICATE, INACTIVE, INSTRUMENT, LINE, PLAN, PLAN_ITEM

DAY = '2026-11-21'
PARAMS = {'report_day': DAY, 'horizon_days': 14, 'min_coverage_percent': 80}


def row(kind, **fields):
    base = {key: None for key in (
        'record_type', 'asset_id', 'asset_name', 'work_area', 'owner', 'asset_status',
        'certificate_id', 'issued_on', 'expires_on', 'line_code', 'line_name', 'plan_code', 'plan_name')}
    base.update(record_type=kind, **fields)
    return base


def instrument(asset_id, status=ACTIVE, owner='一班'):
    return row(INSTRUMENT, asset_id=asset_id, asset_name=f'器具{asset_id}', work_area='机加工区',
               owner=owner, asset_status=status)


def certificate(asset_id, code, issued, expires):
    return row(CERTIFICATE, asset_id=asset_id, certificate_id=code, issued_on=issued, expires_on=expires)


def line(code, name):
    return row(LINE, line_code=code, line_name=name)


def plan(code, name, line_code):
    return row(PLAN, plan_code=code, plan_name=name, line_code=line_code)


def plan_item(plan_code, asset_id):
    return row(PLAN_ITEM, plan_code=plan_code, asset_id=asset_id)


class MinimalCombinationTest(unittest.TestCase):
    def combos(self, groups):
        return sorted(sorted(combo) for combo in _minimal_sets(groups)[0])

    def test_single_plan_needs_any_one_instrument(self):
        self.assertEqual(self.combos([{'a', 'b'}]), [['a'], ['b']])

    def test_shared_instrument_alone_stops_line(self):
        self.assertEqual(self.combos([{'a', 'b'}, {'a', 'c'}]), [['a'], ['b', 'c']])

    def test_covering_plan_never_survives(self):
        self.assertEqual(self.combos([{'a', 'b'}, {'a'}, {'b', 'c'}]), [['a', 'b'], ['a', 'c']])

    def test_disjoint_plans_combine(self):
        self.assertEqual(self.combos([{'a', 'b'}, {'c', 'd'}]),
                         [['a', 'c'], ['a', 'd'], ['b', 'c'], ['b', 'd']])

    def test_no_usable_plan_has_no_combination(self):
        self.assertEqual(self.combos([]), [])

    def test_dropping_one_instrument_always_leaves_a_plan(self):
        groups = [{'a', 'b', 'c'}, {'a', 'b'}, {'b', 'c'}, {'a', 'c'}, {'d', 'e'}]
        for combo in _minimal_sets(groups)[0]:
            self.assertTrue(any(not group <= combo for group in groups), sorted(combo))
            for asset_id in combo:
                reduced = combo - {asset_id}
                self.assertTrue(any(not group <= reduced for group in groups), (sorted(combo), asset_id))


class LineReportTest(unittest.TestCase):
    def context(self, rows):
        return prepare(rows, PARAMS)

    def report(self, rows, code):
        context = self.context(rows)
        return line_report(context, code)

    def test_missing_certificate_marks_plan_pending(self):
        rows = [instrument('A1'), line('L1', '一线'), plan('P1', '方案一', 'L1'), plan_item('P1', 'A1')]
        result = self.report(rows, 'L1')
        self.assertEqual(result['status'], LINE_PENDING)
        self.assertEqual(result['plans'][0]['state'], PLAN_PENDING)
        self.assertEqual(result['combos'], [])

    def test_confirmed_no_plan_marks_line_stopped(self):
        rows = [
            instrument('A1'), certificate('A1', 'C1', '2026-05-01', '2026-11-01'),
            line('L1', '一线'), plan('P1', '方案一', 'L1'), plan_item('P1', 'A1'),
        ]
        result = self.report(rows, 'L1')
        self.assertEqual(result['status'], LINE_STOPPED)
        self.assertEqual(result['plans'][0]['state'], PLAN_BLOCKED)
        self.assertIn('A1 证书已过期', result['plans'][0]['reasons'])

    def test_inactive_instrument_blocks_plan(self):
        rows = [
            instrument('A1', INACTIVE), certificate('A1', 'C1', '2026-05-01', '2027-05-01'),
            line('L1', '一线'), plan('P1', '方案一', 'L1'), plan_item('P1', 'A1'),
        ]
        result = self.report(rows, 'L1')
        self.assertEqual(result['status'], LINE_STOPPED)
        self.assertIn('A1 器具停用', result['plans'][0]['reasons'])

    def test_combination_lists_blocked_plans(self):
        rows = [
            instrument('A1'), instrument('A2'), instrument('A3'),
            certificate('A1', 'C1', '2026-05-01', '2027-05-01'),
            certificate('A2', 'C2', '2026-05-01', '2027-05-01'),
            certificate('A3', 'C3', '2026-05-01', '2027-05-01'),
            line('L1', '一线'),
            plan('P1', '方案一', 'L1'), plan_item('P1', 'A1'), plan_item('P1', 'A2'),
            plan('P2', '方案二', 'L1'), plan_item('P2', 'A2'), plan_item('P2', 'A3'),
        ]
        result = self.report(rows, 'L1')
        self.assertEqual(result['status'], LINE_OK)
        self.assertEqual([combo['instruments'] for combo in result['combos']], [['A2'], ['A1', 'A3']])
        self.assertEqual([plan['plan_code'] for plan in result['combos'][1]['plans']], ['P1', 'P2'])
        self.assertEqual([plan['plan_code'] for plan in result['combos'][0]['plans']], ['P1', 'P2'])
        self.assertEqual([combo['key'] for combo in result['combos']], ['A2', 'A1+A3'])

    def test_certificate_after_report_day_is_ignored(self):
        rows = [
            instrument('A1'), certificate('A1', 'C1', '2026-11-22', '2027-05-22'),
            line('L1', '一线'), plan('P1', '方案一', 'L1'), plan_item('P1', 'A1'),
        ]
        result = self.report(rows, 'L1')
        self.assertEqual(result['plans'][0]['state'], PLAN_PENDING)

    def test_latest_certificate_issued_on_report_day_applies(self):
        rows = [
            instrument('A1'),
            certificate('A1', 'OLD', '2026-05-01', '2026-11-01'),
            certificate('A1', 'NEW', '2026-11-21', '2027-11-21'),
            line('L1', '一线'), plan('P1', '方案一', 'L1'), plan_item('P1', 'A1'),
        ]
        result = self.report(rows, 'L1')
        self.assertEqual(result['plans'][0]['state'], PLAN_OK)

    def test_assembly_reports_progress_and_deduplicates_combos(self):
        context = self.context(demo_rows())
        done = {code: line_report(context, code) for code in context['line_codes']}
        first = assemble(context, done)
        duplicate = dict(done['L-MACH'])
        duplicate['combos'] = duplicate['combos'] + duplicate['combos']
        second = assemble(context, {**done, 'L-MACH': duplicate})
        keys = [combo['key'] for line in second['lines'] if line['line_code'] == 'L-MACH' for combo in line['combos']]
        self.assertEqual(keys, [combo['key'] for line in first['lines'] if line['line_code'] == 'L-MACH' for combo in line['combos']])
        self.assertEqual(second['progress'], {'lines_done': 4, 'lines_total': 4})

    def test_partial_progress_keeps_previous_report(self):
        context = self.context(demo_rows())
        partial = {'L-MACH': line_report(context, 'L-MACH')}
        result = assemble(context, partial)
        self.assertEqual(result['progress'], {'lines_done': 1, 'lines_total': 4})
        self.assertEqual([row['line_code'] for row in result['lines']], ['L-MACH'])


class DemoTest(unittest.TestCase):
    def test_demo_report_matches_business_steps(self):
        result = analyze(demo_rows(), PARAMS)
        self.assertTrue(result['ready'])
        self.assertIn('5/6 台有证书', result['headline'])
        lines = {line['line_code']: line for line in result['lines']}
        self.assertEqual(lines['L-MACH']['status'], LINE_OK)
        self.assertEqual([combo['instruments'] for combo in lines['L-MACH']['combos']], [['QC-103'], ['QC-104']])
        self.assertEqual([combo['instruments'] for combo in lines['L-ASSY']['combos']], [['QC-102'], ['QC-103', 'QC-105']])
        self.assertEqual(lines['L-FINAL']['status'], LINE_PENDING)
        self.assertEqual(lines['L-PACK']['status'], LINE_STOPPED)
        titles = [table['title'] for table in result['tables']]
        self.assertIn('产线最小停线组合', titles)
        combo_rows = next(table for table in result['tables'] if table['title'] == '产线最小停线组合')['rows']
        listed = sum(len(line['combos']) if line['combos'] else 1 for line in result['lines'])
        self.assertEqual(len(combo_rows), listed)
        pack = next(row for row in combo_rows if row['line_code'] == 'L-PACK')
        self.assertEqual(pack['line_status'], LINE_STOPPED)
        self.assertIn('P-07', pack['plans'])

    def test_validation_rejects_unknown_plan_instrument(self):
        rows = demo_rows() + [plan_item('P-01', 'QC-999')]
        with self.assertRaisesRegex(ValueError, 'QC-999'):
            analyze(rows, PARAMS)

    def test_legacy_batch_without_line_columns_still_analyzes(self):
        legacy = [{key: row[key] for key in (
            'record_type', 'asset_id', 'asset_name', 'work_area', 'owner', 'asset_status',
            'certificate_id', 'issued_on', 'expires_on')} for row in demo_rows()
            if row['record_type'] in (INSTRUMENT, CERTIFICATE)]
        result = analyze(legacy, PARAMS)
        self.assertTrue(result['ready'])
        self.assertEqual(result['lines'], [])
        self.assertNotIn('产线最小停线组合', [table['title'] for table in result['tables']])

    def test_validation_rejects_plan_without_line(self):
        rows = demo_rows() + [plan('P-99', '无产线方案', 'L-NONE')]
        with self.assertRaisesRegex(ValueError, 'L-NONE'):
            analyze(rows, PARAMS)


if __name__ == '__main__':
    unittest.main()
