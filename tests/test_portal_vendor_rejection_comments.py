import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import tempfile
import unittest
import zipfile
from unittest.mock import MagicMock, patch
from pathlib import Path

from portal_vendor import rejection_comments as rejection
from portal_vendor import data_filler as engine
from portal_vendor.rejection_comments import RejectionComment, fill_comment, load_comments, load_submissions


class RejectionCommentTests(unittest.TestCase):
    def test_comment_entry_and_existing_text_protection(self):
        record = RejectionComment('a' * 24, 'Student', 'Wrong subject', 'reason.txt')
        page = MagicMock()
        boxes = page.locator.return_value
        boxes.count.return_value = 1
        box = boxes.first
        box.get_attribute.return_value = None
        box.input_value.side_effect = ['', record.comment]
        fill_comment(page, record)
        box.fill.assert_called_once_with(record.comment)
        box.reset_mock()
        box.input_value.side_effect = None
        box.input_value.return_value = 'Existing teacher comment'
        with self.assertRaisesRegex(ValueError, 'already contains'):
            fill_comment(page, record)
        box.fill.assert_not_called()

    def test_comment_limit_does_not_truncate(self):
        page = MagicMock()
        page.locator.return_value.count.return_value = 1
        box = page.locator.return_value.first
        box.input_value.return_value = ''
        box.get_attribute.return_value = '3'
        with self.assertRaisesRegex(ValueError, 'exceeds'):
            fill_comment(page, RejectionComment('a' * 24, 'Student', 'Too long', 'reason.txt'))
        box.fill.assert_not_called()

    def test_zip_selects_only_rejected_and_preserves_unicode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evaluated.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("test/rejected/Student_Name_" + "a" * 24 + "_reason.txt", "\ufeffWrong subject — Chemistry.\nNo marks were given.")
                archive.writestr("test/failed/Other_error.txt", "Timeout")
                archive.writestr("test/faculty_review/Other_reasons.txt", "Check marks")
            records = load_comments(path)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].student_id, "a" * 24)
            self.assertEqual(records[0].student_name, "Student_Name")
            self.assertEqual(records[0].comment, "Wrong subject — Chemistry.\nNo marks were given.")

    def test_duplicate_students_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evaluated.zip"
            with zipfile.ZipFile(path, "w") as archive:
                for name in ("One", "Two"):
                    archive.writestr("test/rejected/" + name + "_" + "a" * 24 + "_reason.txt", "Reason")
            with self.assertRaisesRegex(ValueError, "More than one"):
                load_comments(path)

    def test_folder_and_empty_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "rejected"
            folder.mkdir()
            reason = folder / ("Student_" + "a" * 24 + " (2)_reason.txt")
            reason.write_text("Reason", encoding="utf-8")
            self.assertEqual(load_comments(folder), load_comments(Path(tmp)))
            reason.write_text("  ", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Empty"):
                load_comments(folder)

    def test_malformed_student_id_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "rejected"
            folder.mkdir()
            (folder / "Student_invalid_reason.txt").write_text("Reason", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid rejection filename"):
                load_comments(folder)


class RejectedPairTests(unittest.TestCase):
    sid = 'a' * 24

    def make_pair(self, folder):
        folder = Path(folder)
        (folder / f'Student_{self.sid}_reason.txt').write_text('Wrong subject', encoding='utf-8')
        pdf = folder / f'Different_Name_{self.sid}.pdf'
        pdf.write_bytes(b'%PDF-1.4')
        return pdf

    def test_pair_uses_id_not_name_and_accepts_any_folder_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = self.make_pair(tmp)
            records = load_submissions(tmp)
            self.assertEqual(records[0].paper_path, pdf.resolve())

    def test_id_only_txt_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_pair(tmp)
            Path(tmp, f'Student_{self.sid}_reason.txt').rename(Path(tmp, f'{self.sid}.txt'))
            self.assertEqual(load_submissions(tmp)[0].student_id, self.sid)

    def test_missing_pdf_blocks_all_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_pair(tmp).unlink()
            with self.assertRaisesRegex(ValueError, 'found 0'):
                load_submissions(tmp)

    def test_different_duplicate_pdf_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_pair(tmp)
            Path(tmp, f'Copy_{self.sid}.PDF').write_bytes(b'%PDF-1.5')
            with self.assertRaisesRegex(ValueError, 'Different PDF contents'):
                load_submissions(tmp)

    def test_pdf_without_reason_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_pair(tmp)
            Path(tmp, 'Other_' + 'b' * 24 + '.pdf').write_bytes(b'%PDF-1.4')
            self.assertEqual([r.student_id for r in load_submissions(tmp)], [self.sid])

    def test_identical_duplicates_produce_one_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = self.make_pair(tmp)
            Path(tmp, f'Different_Name_{self.sid} (2).pdf').write_bytes(pdf.read_bytes())
            records = load_submissions(tmp)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].paper_path, pdf.resolve())

    def test_unrelated_malformed_and_duplicate_pdfs_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_pair(tmp)
            for name in ['no-id.pdf', 'b' * 24 + '.pdf', 'b' * 24 + ' (2).pdf',
                         'b' * 24 + '_' + 'c' * 24 + '.pdf']:
                Path(tmp, name).write_bytes(name.encode())
            self.assertEqual([r.student_id for r in load_submissions(tmp)], [self.sid])

    def test_duplicate_comparison_checks_beyond_first_chunk(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = self.make_pair(tmp)
            pdf.write_bytes(b'x' * (1024 * 1024) + b'first')
            Path(tmp, f'Copy_{self.sid}.pdf').write_bytes(b'x' * (1024 * 1024) + b'other')
            with self.assertRaisesRegex(ValueError, 'Different PDF contents'):
                load_submissions(tmp)

    def test_pdf_with_multiple_ids_blocks_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_pair(tmp)
            Path(tmp, self.sid + '_' + 'b' * 24 + '.pdf').write_bytes(b'%PDF-1.4')
            with self.assertRaisesRegex(ValueError, 'exactly one full Student ID'):
                load_submissions(tmp)


class AutomaticRejectionTests(unittest.TestCase):
    def setUp(self):
        self.record = RejectionComment('a' * 24, 'Student', 'Wrong subject', 'reason.txt', Path('paper.pdf'))
        self.page = MagicMock()
        self.page.locator.return_value.filter.return_value.count.return_value = 1
        self.page.locator.return_value.count.return_value = 1
        self.page.locator.return_value.first.input_value.side_effect = ['', self.record.comment]
        self.page.locator.return_value.first.get_attribute.return_value = None
        self.page.locator.return_value.filter.return_value.last.get_by_role.return_value.count.return_value = 1
        self.page.locator.return_value.filter.return_value.last.get_by_role.return_value.evaluate.return_value = []
        self.total = self.page.locator.return_value.filter.return_value.last.locator.return_value
        self.total.count.return_value = 1
        self.total.input_value.return_value = '0'
        self.patches = [patch.object(rejection.browser, 'find_pending_row'), patch.object(engine, 'search_box'),
                        patch.object(rejection.browser, 'upload_paper'), patch.object(engine, 'click_menu_item'),
                        patch.object(engine, 'should_stop', return_value=False),
                        patch.object(engine, 'fill_evaluation_modal'), patch.object(engine, 'ask_continue'),
                        patch.object(engine, 'ask_yes_no')]
        self.mocks = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)
        self.mocks[0].return_value = self.page.locator.return_value.filter.return_value.first
        self.mocks[2].return_value = self.mocks[0].return_value

    @property
    def button(self):
        return self.page.locator.return_value.filter.return_value.last.get_by_role.return_value

    def submit(self, **kwargs):
        rejection.submit_rejection(self.page, self.record, 'Program', '', 'Test', **kwargs)

    def test_automatic_flow_sets_zero_total_without_ratings_or_visible_id_column(self):
        journal = MagicMock()
        self.submit(before_submit=journal)
        self.mocks[2].assert_called_once()
        self.mocks[3].assert_called_once_with(self.page, self.page.locator.return_value.filter.return_value.first, 'View Upload')
        self.page.locator.return_value.first.fill.assert_called_once_with('Wrong subject')
        self.button.click.assert_called_once()
        self.total.fill.assert_called_once_with('0')
        self.total.blur.assert_called_once()
        journal.assert_called_once()
        for mock in self.mocks[5:]:
            mock.assert_not_called()  # No question-marks/ratings flow or manual prompts.
        self.page.locator.return_value.filter.return_value.first.locator.assert_not_called()

    def test_missing_or_ambiguous_total_field_blocks_submit(self):
        self.total.count.return_value = 0
        with self.assertRaisesRegex(RuntimeError, 'exactly one visible Total Marks'):
            self.submit()
        self.button.click.assert_not_called()

    def test_nonzero_or_empty_total_blocks_submit(self):
        for value in ['7', '', 'NaN', 'Infinity']:
            with self.subTest(value=value):
                self.page.locator.return_value.first.input_value.side_effect = ['', self.record.comment]
                self.total.input_value.return_value = value
                with self.assertRaisesRegex(RuntimeError, 'did not retain Total Marks = 0'):
                    self.submit()
                self.button.click.assert_not_called()

    def test_recalculated_total_blocks_submit(self):
        self.total.input_value.side_effect = ['0', '7']
        with self.assertRaisesRegex(RuntimeError, 'did not retain Total Marks = 0'):
            self.submit()
        self.button.click.assert_not_called()

    def test_native_validation_failure_never_marks_submission_uncertain(self):
        self.button.evaluate.return_value = ['Required field']
        journal = MagicMock()
        with self.assertRaisesRegex(RuntimeError, 'Website validation blocked Submit'):
            self.submit(before_submit=journal)
        journal.assert_not_called()
        self.button.click.assert_not_called()

    def test_ambiguous_pending_rows_never_upload(self):
        self.mocks[0].side_effect = LookupError('Duplicate website records')
        with self.assertRaisesRegex(RuntimeError, 'Duplicate website records'):
            self.submit()
        self.mocks[2].assert_not_called()
        self.button.click.assert_not_called()

    def test_wrong_search_value_never_uploads(self):
        self.mocks[0].side_effect = ValueError('The site did not retain the full Student ID search.')
        with self.assertRaisesRegex(RuntimeError, 'full Student ID search'):
            self.submit()
        self.mocks[2].assert_not_called()

    def test_submit_timeout_is_unverified_and_clicked_only_once(self):
        self.button.click.side_effect = engine.PWTimeout('No response')
        with self.assertRaises(rejection.SubmissionUnverified):
            self.submit()
        self.button.click.assert_called_once()

    def test_missing_success_confirmation_is_unverified(self):
        # textarea visible, stale toast hidden, then success toast never appears.
        self.page.locator.return_value.first.wait_for.side_effect = [None, None, engine.PWTimeout('No success')]
        with self.assertRaises(rejection.SubmissionUnverified):
            self.submit()
        self.button.click.assert_called_once()

    def test_old_success_toast_blocks_submit(self):
        self.page.locator.return_value.first.wait_for.side_effect = [None, engine.PWTimeout('Old toast')]
        with self.assertRaisesRegex(RuntimeError, 'prepare Submit'):
            self.submit()
        self.button.click.assert_not_called()

    def test_stop_after_upload_prevents_submit(self):
        self.mocks[4].side_effect = [False, True]
        with self.assertRaises(engine.AbortRun):
            self.submit()
        self.button.click.assert_not_called()

    def test_existing_comment_blocks_submit(self):
        self.page.locator.return_value.first.input_value.side_effect = None
        self.page.locator.return_value.first.input_value.return_value = 'Another comment'
        with self.assertRaisesRegex(RuntimeError, 'already contains'):
            self.submit()
        self.button.click.assert_not_called()


class RejectionResumeTests(unittest.TestCase):
    def test_journal_separates_program_test_category_and_student(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(rejection, 'PROGRESS_FILE', Path(tmp, 'progress.csv')):
            key = ('Program', 'Batch', 'Test', 'a' * 24)
            rejection._save_progress(key, 'Unverified')
            self.assertEqual(rejection._progress()[key], 'Unverified')
            rejection._save_progress(key, 'Done')
            self.assertEqual(rejection._progress()[key], 'Done')
            self.assertNotIn(('Other', *key[1:]), rejection._progress())

    def test_new_run_records_success_or_continues_after_unverified_submit(self):
        for confirmed in (True, False):
            with self.subTest(confirmed=confirmed), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                records = []
                for sid in ['a' * 24, 'b' * 24]:
                    pdf = folder / f'{sid}.pdf'
                    pdf.write_bytes(b'%PDF-1.4')
                    records.append(RejectionComment(sid, 'Student', 'Reason', 'reason.txt', pdf))
                def submit(*args, before_submit):
                    before_submit()
                    if not confirmed:
                        raise rejection.SubmissionUnverified('No server confirmation')
                with patch.object(rejection, '_progress', return_value={}), \
                     patch.object(rejection, '_save_progress') as save, \
                     patch.object(rejection, 'submit_rejection', side_effect=submit) as send, \
                     patch('portal_vendor.match_paper.site_session') as session, \
                     patch.object(engine, 'wait_for_manual_login'), \
                     patch.object(engine, 'open_evaluate'), \
                     patch.object(engine, 'save_screenshot'), \
                     patch.object(engine, 'should_stop', return_value=False), \
                     patch.object(rejection, 'Path', side_effect=lambda x: folder / x):
                    session.return_value.__enter__.return_value = (MagicMock(), MagicMock())
                    summary = rejection.run_comments(records, 'Program', '', 'Test')
                if confirmed:
                    self.assertEqual(summary['counts']['Done'], 2)
                    self.assertEqual([c.args[1] for c in save.call_args_list], ['Unverified', 'Done'] * 2)
                    self.assertEqual(send.call_count, 2)
                else:
                    self.assertEqual(summary['counts']['Unverified'], 2)
                    self.assertFalse(summary['stopped'])
                    self.assertEqual([c.args[1] for c in save.call_args_list], ['Unverified'] * 2)
                    self.assertEqual(send.call_count, 2)

    def test_rerun_never_repeats_done_or_unverified_submission(self):
        for previous, expected in [('Done', 'Done earlier'), ('Unverified', 'Unverified')]:
            with self.subTest(previous=previous), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                sid = 'a' * 24
                pdf = folder / f'{sid}.pdf'
                pdf.write_bytes(b'%PDF-1.4')
                record = RejectionComment(sid, 'Student', 'Reason', 'reason.txt', pdf)
                key = ('Program', '', 'Test', sid)
                with patch.object(rejection, '_progress', return_value={key: previous}), \
                     patch.object(rejection, 'submit_rejection') as submit, \
                     patch('portal_vendor.match_paper.site_session') as session, \
                     patch.object(engine, 'wait_for_manual_login'), \
                     patch.object(engine, 'save_screenshot'), \
                     patch.object(engine, 'should_stop', return_value=False), \
                     patch.object(rejection, 'Path', side_effect=lambda x: folder / x):
                    session.return_value.__enter__.return_value = (MagicMock(), MagicMock())
                    summary = rejection.run_comments([record], 'Program', '', 'Test')
                self.assertEqual(summary['counts'][expected], 1)
                submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
