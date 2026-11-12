import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

from core.models import BidNotice
from core.storage import NoticeStorage
from core.alio_collector import AlioNoticeStorage, AlioCollector
from core.eiaa_collector import EiaaNoticeStorage
from core.collector import NaraCollector

ROOT = Path(__file__).resolve().parents[1]


class DeliveryStateTests(unittest.TestCase):
    def test_nara_failed_delivery_is_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = NoticeStorage(str(Path(tmp) / 'state.db'))
            n = BidNotice('1', '00', '환경영향평가', '용역', '기관', '기관')
            store.save_notice(n, notified=False)
            self.assertTrue(store.is_already_seen(n.unique_id))
            self.assertFalse(store.is_already_seen(n.unique_id, notified_only=True))
            store.mark_as_notified(n.unique_id)
            self.assertTrue(store.is_already_seen(n.unique_id, notified_only=True))

    def test_alio_failed_then_success_not_suppressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AlioNoticeStorage(str(Path(tmp) / 'state.db'))
            args = dict(seq='1', title='환경영향평가', agency='기관', budget=0,
                        post_date='2026.11.12', close_date='', detail_url='url',
                        source_url='', files=[], matched_keywords=[], env_category='환경')
            store.save_notice(**args, notified=False)
            self.assertFalse(store.is_already_seen('1', '기관', '환경영향평가', '2026.11.12', ''))
            store.save_notice(**args, notified=True)
            self.assertTrue(store.is_already_seen('1', '기관', '환경영향평가', '2026.11.12', ''))

    def test_eiaa_failed_then_success_not_suppressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EiaaNoticeStorage(str(Path(tmp) / 'state.db'))
            args = dict(seq='1', title='환경영향평가', agency='기관', post_date='2026-11-12',
                        detail_url='url', matched_keywords=[], env_category='환경')
            store.save_notice(**args, notified=False)
            self.assertFalse(store.is_already_seen('1', '환경영향평가', '2026-11-12'))
            store.save_notice(**args, notified=True)
            self.assertTrue(store.is_already_seen('1', '환경영향평가', '2026-11-12'))

    def test_nara_send_failure_retries_then_deduplicates(self):
        from cloud_run import run_cloud_check
        with tempfile.TemporaryDirectory() as tmp:
            store = NoticeStorage(str(Path(tmp) / 'state.db'))
            notice = BidNotice('retry-1', '00', '환경영향평가 용역', '용역', '기관', '기관')
            collector = Mock(search_errors=[])
            collector.fetch_all.return_value = [notice]
            notifier = Mock()
            notifier.notify_all.side_effect = [{'ntfy': False}, {'ntfy': True}]
            with patch('cloud_run.NoticeStorage', return_value=store), \
                 patch('cloud_run.NaraCollector', return_value=collector), \
                 patch('cloud_run.MultiChannelNotifier', return_value=notifier), \
                 patch('cloud_run.requests.get', return_value=Mock(status_code=200, text='')):
                with self.assertRaises(RuntimeError):
                    run_cloud_check(include_auxiliary=False)
                self.assertEqual(run_cloud_check(include_auxiliary=False), 1)
                self.assertEqual(run_cloud_check(include_auxiliary=False), 0)
                self.assertEqual(notifier.notify_all.call_count, 2)

    def test_source_entry_does_not_run_auxiliary(self):
        import run_source
        with patch('cloud_run.run_cloud_check') as run:
            run_source.run('nara')
            run.assert_called_once_with(include_auxiliary=False)

    def test_dry_run_does_not_publish_or_change_original_db(self):
        import run_source
        with patch('run_source.subprocess.run') as run:
            run_source.run('alio', dry_run=True)
            env = run.call_args.kwargs['env']
            self.assertEqual(env['MONITOR_NO_SEND'], '1')
            self.assertNotEqual(run.call_args.kwargs['cwd'], str(ROOT))


class SearchTests(unittest.TestCase):
    def test_twenty_searches_and_retry_preserve_query_scope(self):
        calls = []
        def response(url, **kw):
            calls.append(kw['json']['dlSrchParamM'].copy())
            return Mock(raise_for_status=Mock(), json=lambda: {'dlTotalSrchL': []})
        with patch('core.collector.requests.post', side_effect=response):
            c = NaraCollector()
            self.assertEqual(c.fetch_from_unty_search(days_back=2), [])
        self.assertEqual(len(calls), 20)
        self.assertEqual({x['untySrchSeCd'] for x in calls}, {'DC', 'BD', 'DD', 'BK', 'BUK'})
        self.assertEqual({x['bizNm'] for x in calls}, {'환경영향', '전략환경', '소규모환경', '사후환경'})
        self.assertFalse(c.search_errors)

    def test_search_failure_is_visible_and_retried(self):
        with patch('core.collector.requests.post', side_effect=TimeoutError) as post:
            c = NaraCollector()
            self.assertEqual(c.fetch_from_unty_search(2), [])
            self.assertEqual(post.call_count, 40)
            self.assertEqual(len(c.search_errors), 20)

    def test_cloud_search_defers_budget_without_dropping_results(self):
        item = {'bizNo': '1', 'bizNm': '환경영향평가', 'bizOrd': '00'}
        def response(url, **kw):
            items = [item] if kw['json']['dlSrchParamM']['untySrchSeCd'] == 'DD' else []
            return Mock(raise_for_status=Mock(), json=lambda: {'dlTotalSrchL': items})
        c = NaraCollector(enrich_budgets=False)
        with patch('core.collector.requests.post', side_effect=response), \
             patch.object(c, '_fetch_oder_plan_budget') as detail:
            notices = c.fetch_from_unty_search(2)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].stage, '발주계획')
        detail.assert_not_called()

    def test_alio_error_is_not_empty_success(self):
        c = AlioCollector()
        with patch('urllib.request.urlopen', side_effect=TimeoutError):
            self.assertEqual(c.fetch_page(), [])
        self.assertTrue(c.request_errors)


class WorkflowTests(unittest.TestCase):
    def test_workflow_yaml_and_db_ownership(self):
        for path in (ROOT / '.github/workflows').glob('*.yml'):
            yaml.safe_load(path.read_text(encoding='utf-8'))
        for source in ('alio', 'eiaa'):
            s = (ROOT / f'.github/workflows/{source}.yml').read_text(encoding='utf-8')
            self.assertIn(f'group: nara-env-{source}-monitor', s)
            self.assertIn('cancel-in-progress: false', s)
            self.assertIn('ref: main', s)
            self.assertIn(f'persist_state.py {source}', s)
            self.assertNotIn('git pull', s)
        self.assertIn('workflow_dispatch:', (ROOT / '.github/workflows/main.yml').read_text(encoding='utf-8'))


class PersistenceTests(unittest.TestCase):
    def test_independent_db_pushes_and_conflict_refusal(self):
        def cmd(folder, *args, check=True):
            result = subprocess.run(['git', *args], cwd=folder, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if check and result.returncode:
                raise RuntimeError(result.stderr)
            return result
        def db(folder, source, val):
            with sqlite3.connect(Path(folder) / f'{source}_notices.db') as con:
                con.execute('CREATE TABLE IF NOT EXISTS state (v TEXT)')
                con.execute('INSERT INTO state VALUES (?)', (val,))
            con.close()
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            origin = Path(tmp) / 'origin.git'
            a, b = Path(tmp) / 'a', Path(tmp) / 'b'
            cmd(tmp, 'init', '--bare', str(origin))
            cmd(tmp, 'clone', '--no-hardlinks', str(origin), str(a))
            cmd(a, 'checkout', '-b', 'main')
            cmd(a, 'config', 'user.name', 'test')
            cmd(a, 'config', 'user.email', 'test@example.com')
            for source in ('nara', 'alio', 'eiaa'):
                db(a, source, 'initial')
            cmd(a, 'add', '.')
            cmd(a, 'commit', '-m', 'initial')
            cmd(a, 'push', 'origin', 'main')
            cmd(tmp, 'clone', '--no-hardlinks', '--branch', 'main', str(origin), str(b))
            db(a, 'nara', 'new nara')
            db(b, 'alio', 'new alio')
            script = ROOT / 'scripts/persist_state.py'
            for folder, source in [(a, 'nara'), (b, 'alio')]:
                subprocess.run([sys.executable, str(script), source], cwd=folder,
                               check=True, capture_output=True)
            cmd(b, 'fetch', 'origin', 'main')
            changed = cmd(b, 'diff', '--name-only', 'HEAD', 'FETCH_HEAD').stdout.splitlines()
            self.assertEqual(set(changed), {'nara_notices.db', 'alio_notices.db'})
            # A stale same-source writer must fail, never replace the published DB.
            before = cmd(b, 'rev-parse', 'FETCH_HEAD').stdout
            db(b, 'nara', 'stale overwrite')
            result = subprocess.run([sys.executable, str(script), 'nara'], cwd=b,
                                    capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('refusing overwrite', result.stderr)
            cmd(b, 'fetch', 'origin', 'main')
            self.assertEqual(cmd(b, 'rev-parse', 'FETCH_HEAD').stdout, before)


if __name__ == '__main__':
    unittest.main()
