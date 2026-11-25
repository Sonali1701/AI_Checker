import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import unittest
from unittest.mock import patch

from portal_vendor import data_filler as engine
from test_portal_vendor_allotment import PaginatedStudents


EMAIL = 'evaluator.b4@pw.live'
LABEL = 'Sonali Singh (' + EMAIL + ')'


class EvaluatorRecoveryTests(unittest.TestCase):
    def test_submitted_fifth_bundle_recovers_and_only_submits_each_bundle_once(self):
        batches = [[[f'Student-{p}-{s}', ''] for s in range(50)] for p in range(5)]
        site = PaginatedStudents([batches.pop(0)])
        site.status = 'Submitted'
        site.table.locator.return_value.all_inner_texts.side_effect = lambda: [r[0] for r in site.pages[0]]
        site.after_submit = lambda: site.pages.__setitem__(0, batches.pop(0) if batches else [])
        results = []
        with site.patches(), patch.object(engine, '_allotment_rows', return_value=site.table), \
                patch.object(engine, 'select_evaluator', side_effect=[LABEL] * 4 + [
                    engine.EvaluatorListUnavailable('empty'), LABEL]) as select, \
                patch.object(engine, 'refresh_students', side_effect=site.refresh) as refresh:
            engine.allot_submitted(site.page, 'Maths', EMAIL, 'deadline', results, None)
        self.assertEqual(len(site.submissions), 5)
        self.assertEqual(select.call_count, 6)
        self.assertEqual(refresh.call_count, 6)  # Five normal updates plus one recovery refresh.
        self.assertEqual(len(results), 250)
        self.assertEqual(len({r['Student_ID'] for r in results}), 250)
        self.assertTrue(all(r['Result'] == engine.DONE for r in results))

    def test_recovery_restores_original_students_on_another_page_and_at_new_indices(self):
        site = PaginatedStudents([[['Other', 'Shweta Ruhil']], [['Arnav', 'Shweta Ruhil']]])
        targets = engine._allotment_snapshot(site.page)
        site.selected = {0}
        def reload_and_move(*_):
            site.pages = [[['First', 'Shweta Ruhil']], [['New', 'Shweta Ruhil'], ['Arnav', 'Shweta Ruhil']]]
            site.refresh()
        with site.patches(), patch.object(engine, 'refresh_students', side_effect=reload_and_move), \
                patch.object(engine, 'select_evaluator', side_effect=[engine.EvaluatorListUnavailable('empty'), LABEL]):
            selected = engine._select_evaluator_with_refresh(site.page, EMAIL, engine.REASSIGNABLE, targets, None)
        self.assertEqual(selected, LABEL)
        self.assertEqual(site.index, 1)
        self.assertEqual(site.selected, {1})
        self.assertEqual(site.submissions, [])

    def test_persistent_empty_list_stops_after_exactly_two_full_refreshes(self):
        site = PaginatedStudents([[['Arnav', 'Shweta Ruhil']]])
        targets = engine._allotment_snapshot(site.page)
        site.selected = {0}
        with site.patches(), patch.object(engine, 'select_evaluator', side_effect=engine.EvaluatorListUnavailable('empty')) as select, \
                patch.object(engine, 'refresh_students', side_effect=site.refresh) as refresh:
            with self.assertRaisesRegex(RuntimeError, 'after 2 automatic page refreshes'):
                engine._select_evaluator_with_refresh(site.page, EMAIL, engine.REASSIGNABLE, targets, None)
        self.assertEqual(select.call_count, 3)
        self.assertEqual(refresh.call_count, 2)
        self.assertEqual(site.submissions, [])

    def test_populated_list_missing_email_does_not_trigger_refresh(self):
        site = PaginatedStudents([[['Arnav', 'Shweta Ruhil']]])
        targets = engine._allotment_snapshot(site.page)
        with site.patches(), patch.object(engine, 'select_evaluator', side_effect=LookupError('email absent')), \
                patch.object(engine, 'refresh_students') as refresh:
            with self.assertRaises(LookupError):
                engine._select_evaluator_with_refresh(site.page, EMAIL, engine.REASSIGNABLE, targets, None)
        refresh.assert_not_called()

    def test_disappeared_student_stops_without_selecting_a_replacement(self):
        site = PaginatedStudents([[['Arnav', 'Shweta Ruhil']]])
        targets = engine._allotment_snapshot(site.page)
        def reload(*_):
            site.pages[0] = [['Another student', 'Shweta Ruhil']]
            site.refresh()
        with site.patches(), patch.object(engine, 'refresh_students', side_effect=reload):
            with self.assertRaisesRegex(RuntimeError, 'exact student bundle'):
                engine._restore_evaluator_bundle(site.page, targets, engine.REASSIGNABLE, None)
        self.assertEqual(site.selected, set())
        self.assertEqual(site.submissions, [])

    def test_changed_status_stops_recovery(self):
        site = PaginatedStudents([[['Arnav', 'Shweta Ruhil']]])
        targets = engine._allotment_snapshot(site.page)
        def reload(*_):
            site.status = 'InReview'
            site.refresh()
        with site.patches(), patch.object(engine, 'refresh_students', side_effect=reload):
            with self.assertRaisesRegex(RuntimeError, 'statuses changed'):
                engine._restore_evaluator_bundle(site.page, targets, engine.REASSIGNABLE, None)
        self.assertEqual(site.selected, set())

    def test_cancelled_recovery_never_refreshes_or_submits(self):
        site = PaginatedStudents([[['Arnav', 'Shweta Ruhil']]])
        targets = engine._allotment_snapshot(site.page)
        with site.patches(), patch.object(engine, 'should_stop', return_value=True), \
                patch.object(engine, 'refresh_students') as refresh:
            with self.assertRaises(engine.AbortRun):
                engine._select_evaluator_with_refresh(site.page, EMAIL, engine.REASSIGNABLE, targets, None)
        refresh.assert_not_called()
        self.assertEqual(site.submissions, [])

    def test_bootstrap_and_reassignment_both_recover_without_losing_results(self):
        site = PaginatedStudents([[['Arnav', 'Shweta Ruhil']]])
        results = []
        with site.patches(), patch.object(engine, 'select_evaluator', side_effect=[
                engine.EvaluatorListUnavailable('empty'), LABEL,
                engine.EvaluatorListUnavailable('empty'), LABEL]):
            engine.allot_in_evaluation(site.page, 'Maths', EMAIL, 'deadline', results, None)
        self.assertEqual(site.submissions, [0])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['Result'], engine.DONE)


if __name__ == '__main__':
    unittest.main()
