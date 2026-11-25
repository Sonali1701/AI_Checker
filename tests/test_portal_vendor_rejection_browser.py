import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from portal_vendor import data_filler as engine
from portal_vendor import rejection_browser as browser


class AdaptiveSearchTests(unittest.TestCase):
    def test_failed_search_request_is_not_accepted_as_empty_results(self):
        with patch.object(engine, 'should_stop', return_value=False):
            with self.assertRaisesRegex(TimeoutError, 'request failed'):
                browser.wait_search_ready(MagicMock(), {'seen': True, 'pending': set(), 'failed': True})

    def test_successful_search_does_not_refresh(self):
        rows = MagicMock()
        rows.count.return_value = 1
        with patch.object(browser, 'search_rows', return_value=rows), patch.object(browser, 'reopen_pending') as refresh:
            self.assertIs(browser.find_pending_row(MagicMock(), 'Program', '', 'Test', 'Student'), rows.first)
        refresh.assert_not_called()

    def test_waits_for_spinner_longer_than_old_four_second_limit(self):
        clock = {'time': 0.0}
        page = MagicMock()
        page.wait_for_timeout.side_effect = lambda ms: clock.update(time=clock['time'] + ms / 1000)
        page.evaluate.side_effect = lambda *args: {'loading': clock['time'] < 6, 'text': 'Student'}
        with patch.object(browser.time, 'monotonic', side_effect=lambda: clock['time']), patch.object(engine, 'should_stop', return_value=False):
            browser.wait_search_ready(page, {'seen': True, 'pending': set()})
        self.assertGreaterEqual(clock['time'], 6.9)
        self.assertLess(clock['time'], 8)

    def test_waits_for_network_even_when_table_is_temporarily_empty(self):
        clock = {'time': 0.0}
        traffic = {'seen': True, 'pending': {'search'}}
        page = MagicMock()
        def wait(ms):
            clock['time'] += ms / 1000
            if clock['time'] >= 5:
                traffic['pending'].clear()
        page.wait_for_timeout.side_effect = wait
        page.evaluate.return_value = {'loading': False, 'text': ''}
        with patch.object(browser.time, 'monotonic', side_effect=lambda: clock['time']), patch.object(engine, 'should_stop', return_value=False):
            browser.wait_search_ready(page, traffic)
        self.assertGreater(clock['time'], 5.7)

    def test_persistent_spinner_raises_loading_timeout_not_missing_student(self):
        clock = {'time': 0.0}
        page = MagicMock()
        page.wait_for_timeout.side_effect = lambda ms: clock.update(time=clock['time'] + ms / 1000)
        page.evaluate.return_value = {'loading': True, 'text': ''}
        with patch.object(browser.time, 'monotonic', side_effect=lambda: clock['time']), patch.object(engine, 'should_stop', return_value=False), patch.object(browser, 'SEARCH_TIMEOUT_S', 1):
            with self.assertRaisesRegex(TimeoutError, 'absence was not assumed'):
                browser.wait_search_ready(page, {'seen': True, 'pending': set()})

    def test_cancel_during_wait(self):
        with patch.object(engine, 'should_stop', return_value=True):
            with self.assertRaises(engine.AbortRun):
                browser.wait_search_ready(MagicMock(), {'seen': True, 'pending': set()})

    def test_network_listeners_are_removed_on_error(self):
        page = MagicMock()
        with self.assertRaises(ValueError):
            with browser.requests_in_flight(page):
                raise ValueError('test')
        self.assertEqual(page.on.call_count, page.remove_listener.call_count)

    def test_empty_search_recovers_after_one_refresh(self):
        empty, found = MagicMock(), MagicMock()
        empty.count.return_value = 0
        found.count.return_value = 1
        with patch.object(browser, 'search_rows', side_effect=[empty, found]), patch.object(browser, 'reopen_pending') as refresh:
            result = browser.find_pending_row(MagicMock(), 'Program', '', 'Test', 'Student')
        self.assertIs(result, found.first)
        refresh.assert_called_once()

    def test_loading_timeout_retries_but_does_not_count_stale_rows(self):
        with patch.object(browser, 'search_rows', side_effect=TimeoutError('Still loading')), patch.object(browser, 'reopen_pending') as refresh:
            with self.assertRaisesRegex(TimeoutError, 'Still loading'):
                browser.find_pending_row(MagicMock(), 'Program', '', 'Test', 'Student')
        refresh.assert_called_once()

    def test_duplicate_site_records_are_not_chosen(self):
        rows = MagicMock()
        rows.count.return_value = 2
        with patch.object(browser, 'search_rows', return_value=rows), patch.object(browser, 'reopen_pending') as refresh:
            with self.assertRaisesRegex(LookupError, 'Duplicate website records: 2'):
                browser.find_pending_row(MagicMock(), 'Program', '', 'Test', 'Student')
        refresh.assert_not_called()

    def test_missing_pending_record_is_located_in_evaluated_without_upload(self):
        empty, found = MagicMock(), MagicMock()
        empty.count.return_value = 0
        found.count.return_value = 1
        page = MagicMock()
        with patch.object(browser, 'search_rows', side_effect=[empty, empty, empty, found, empty]), patch.object(browser, 'reopen_pending'), patch.object(browser, 'wait_search_ready'):
            with self.assertRaisesRegex(LookupError, 'Not Pending: matching test is in Evaluated'):
                browser.find_pending_row(page, 'Program', '', 'Test', 'Student')


class UploadRecoveryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.pdf = Path(folder.name, 'test.pdf')
        self.pdf.write_bytes(b'%PDF')
        self.page = MagicMock()
        self.row = MagicMock()
        self.restore = MagicMock(return_value=self.row)
        self.stop = patch.object(engine, 'should_stop', return_value=False)
        self.stop.start()
        self.addCleanup(self.stop.stop)

    def upload(self):
        return browser.upload_paper(self.page, self.row, self.pdf, self.restore)

    def test_normal_upload_only_sends_once(self):
        with patch.object(browser, 'has_view_upload', return_value=False), patch.object(engine, 'click_menu_item'), patch.object(engine, 'close_row_menu'):
            self.upload()
        self.page.expect_file_chooser.return_value.__enter__.return_value.value.set_files.assert_called_once()
        self.restore.assert_not_called()

    def test_missed_toast_is_verified_by_new_view_upload_without_resending(self):
        self.page.locator.return_value.first.wait_for.side_effect = [None, engine.PWTimeout('toast missed')]
        with patch.object(browser, 'has_view_upload', side_effect=[False, True]), patch.object(engine, 'click_menu_item'):
            self.upload()
        self.page.expect_file_chooser.return_value.__enter__.return_value.value.set_files.assert_called_once()
        self.restore.assert_called_once()

    def test_existing_view_upload_does_not_prove_new_upload_success(self):
        self.page.locator.return_value.first.wait_for.side_effect = [None, engine.PWTimeout('toast missed')]
        with patch.object(browser, 'has_view_upload', return_value=True), patch.object(engine, 'click_menu_item'):
            with self.assertRaisesRegex(RuntimeError, 'could not be verified'):
                self.upload()
        self.page.expect_file_chooser.return_value.__enter__.return_value.value.set_files.assert_called_once()

    def test_failed_controls_retry_once_before_file_selection(self):
        with patch.object(browser, 'has_view_upload', side_effect=[engine.PWTimeout('menu'), False]), patch.object(engine, 'click_menu_item'), patch.object(engine, 'close_row_menu'):
            self.upload()
        self.restore.assert_called_once()
        self.page.expect_file_chooser.return_value.__enter__.return_value.value.set_files.assert_called_once()

    def test_file_selection_timeout_never_resends_pdf(self):
        self.page.expect_file_chooser.return_value.__enter__.return_value.value.set_files.side_effect = engine.PWTimeout('upload in flight')
        with patch.object(browser, 'has_view_upload', side_effect=[False, False]), patch.object(engine, 'click_menu_item'):
            with self.assertRaisesRegex(RuntimeError, 'could not be verified'):
                self.upload()
        self.page.expect_file_chooser.return_value.__enter__.return_value.value.set_files.assert_called_once()

    def test_failed_controls_stop_after_one_retry(self):
        with patch.object(browser, 'has_view_upload', side_effect=engine.PWTimeout('menu')):
            with self.assertRaisesRegex(RuntimeError, 'failed twice before file selection'):
                self.upload()
        self.restore.assert_called_once()
        self.page.expect_file_chooser.assert_not_called()


if __name__ == '__main__':
    unittest.main()
