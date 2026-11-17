import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import requests

from core.models import BidNotice
from core.storage import NoticeStorage
from core.notifier import MultiChannelNotifier
from core.opening import check_result, init_watch, marker, result_link, run_openings, is_historical


ROW = dict(bidPbancNo='R26BK01813969', bidPbancOrd='000', bidClsfNo='0',
           bidPrgrsOrd='001', prcmBsneSeCd='05', bidPgstCd='개찰완료', onbsPrnmntDt='2099/01/01 11:00')
RESULT = {'url': result_link(ROW), 'status': '개찰완료', 'row': ROW}


class OpeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = NoticeStorage(str(Path(self.tmp.name) / 'state.db'))
        self.n = BidNotice(ROW['bidPbancNo'], '00', '환경영향평가', '기술용역', '기관', '기관')
        self.store.save_notice(self.n, notified=True)
        self.notifier = Mock()
        self.notifier.send_opening.return_value = True
        self.poll = patch('core.opening.requests.get', return_value=Mock(text='', raise_for_status=Mock()))
        self.poll.start(); self.addCleanup(self.poll.stop)

    def state(self):
        with self.store._get_conn() as db:
            return [dict(r) for r in db.execute('SELECT * FROM opening_watch')]

    def due(self):
        with self.store._get_conn() as db:
            db.execute('UPDATE opening_watch SET next_check=0'); db.commit()

    def test_only_delivered_bids_and_normalized_sequences(self):
        for stage, sent in [('입찰공고', False), ('발주계획', True), ('사전규격', True)]:
            self.store.save_notice(BidNotice(stage, '000', '환경', '용역', '', '', stage=stage), sent)
        self.store.save_notice(BidNotice(self.n.bid_no, '000', '환경', '용역', '', ''), True)
        init_watch(self.store); init_watch(self.store)
        self.assertEqual(len(self.state()), 1)
        self.assertEqual(self.state()[0]['bid_seq'], '000')

    def test_first_result_once_across_reopened_database(self):
        with patch('core.opening.check_result', return_value=RESULT) as check:
            self.assertFalse(run_openings(self.store, self.notifier, 'test'))
            self.store = NoticeStorage(self.store.db_path)
            self.assertFalse(run_openings(self.store, self.notifier, 'test'))
            self.assertEqual(check.call_count, 1)
        self.notifier.send_opening.assert_called_once()
        self.assertEqual(self.state()[0]['notified'], 1)

    def test_failed_delivery_cached_and_retried(self):
        self.notifier.send_opening.side_effect = [False, True]
        with patch('core.opening.check_result', return_value=RESULT) as check:
            self.assertTrue(run_openings(self.store, self.notifier, 'test'))
            self.assertEqual(self.state()[0]['notified'], 0)
            self.due()
            self.assertFalse(run_openings(self.store, self.notifier, 'test'))
            self.assertEqual(check.call_count, 1)
        self.assertEqual(self.notifier.send_opening.call_count, 2)

    def test_acknowledged_timeout_or_unpublished_db_reconciles(self):
        self.poll.stop()
        receipt = json.dumps({'event': 'message', 'message': marker(self.n.bid_no, '000')})
        with patch('core.opening.requests.get', return_value=Mock(text=receipt)), \
             patch('core.opening.check_result', return_value=RESULT):
            run_openings(self.store, self.notifier, 'test')
        self.notifier.send_opening.assert_not_called()
        self.assertEqual(self.state()[0]['notified'], 1)

    def test_unavailable_receipts_never_send(self):
        self.poll.stop()
        with patch('core.opening.requests.get', side_effect=requests.Timeout):
            self.assertTrue(run_openings(self.store, self.notifier, 'test'))
        self.notifier.send_opening.assert_not_called()
        self.assertEqual(self.state()[0]['notified'], 0)

    def test_query_error_is_not_empty_success(self):
        with patch('core.opening.check_result', side_effect=requests.Timeout):
            self.assertTrue(run_openings(self.store, self.notifier, 'test'))
        self.assertEqual(self.state()[0]['last_error'], 'Timeout')
        self.notifier.send_opening.assert_not_called()

    def test_unpublished_result_keeps_tracking(self):
        with patch('core.opening.check_result', return_value=None):
            self.assertFalse(run_openings(self.store, self.notifier, 'test'))
        self.assertEqual(self.state()[0]['notified'], 0)
        self.notifier.send_opening.assert_not_called()

    def test_historical_result_never_sent_even_when_cached(self):
        old = dict(RESULT, row=dict(ROW, onbsPrnmntDt='2026/11/17 11:00'))
        init_watch(self.store)
        with self.store._get_conn() as db:
            db.execute('UPDATE opening_watch SET result=?', (json.dumps(old),)); db.commit()
        with patch('core.opening.check_result') as check:
            run_openings(self.store, self.notifier, 'test')
            run_openings(self.store, self.notifier, 'test')
        check.assert_not_called()
        self.notifier.send_opening.assert_not_called()
        self.assertEqual(self.state()[0]['suppressed'],1)
        self.assertEqual(self.state()[0]['notified'],0)

    def test_future_and_unknown_opening_dates(self):
        self.assertFalse(is_historical(RESULT))
        self.assertTrue(is_historical(dict(RESULT,row={})))
        self.assertTrue(is_historical(dict(RESULT,row=dict(ROW,onbsPrnmntDt='invalid'))))

    def test_future_deadline_skips_queries(self):
        with self.store._get_conn() as db:
            db.execute("UPDATE notices SET close_date='2099/12/31 10:00'"); db.commit()
        with patch('core.opening.check_result') as check:
            run_openings(self.store, self.notifier, 'test')
        check.assert_not_called()

    def test_scan_is_bounded_and_fair(self):
        for i in range(10):
            self.store.save_notice(BidNotice(f'R26BK{i:08}', '000', '환경', '용역', '', ''), True)
        with patch('core.opening.check_result', return_value=None) as check:
            run_openings(self.store, self.notifier, 'test', limit=4)
            first = {c.args[0]['bid_no'] for c in check.call_args_list}
            self.assertEqual(len(first), 4)
            check.reset_mock()
            run_openings(self.store, self.notifier, 'test', limit=4)
            self.assertFalse(first & {c.args[0]['bid_no'] for c in check.call_args_list})

    def test_ntfy_payload_and_timeout(self):
        n = MultiChannelNotifier({'ntfy_topic': 'test'})
        row = dict(bid_no=self.n.bid_no,bid_seq='000',title='환경',order_agency='기관')
        with patch('core.notifier.requests.post', return_value=Mock(status_code=200)) as post:
            self.assertTrue(n.send_opening(row, RESULT, marker(self.n.bid_no,'000')))
            payload = post.call_args.kwargs['json']
            self.assertEqual(payload['click'], RESULT['url'])
            self.assertEqual(payload['actions'][0]['url'], RESULT['url'])
            self.assertIn(marker(self.n.bid_no,'000'),payload['message'])
        with patch('core.notifier.requests.post', side_effect=requests.ReadTimeout) as post:
            self.assertFalse(n.send_opening(row, RESULT, 'event'))
            post.assert_called_once()


class ScreenContractTests(unittest.TestCase):
    def session(self, rows, landing='"menuCangVal" : "PBOC007_01"'):
        session = Mock()
        session.headers = {}
        session.get.return_value = Mock(text=landing)
        session.post.return_value = Mock(json=lambda: {'ErrorCode': 0, 'result': {'onbsRsltClsfList': rows}})
        context = Mock(__enter__=Mock(return_value=session), __exit__=Mock(return_value=False))
        return session, context

    def test_wrong_order_waiting_and_wrong_number_do_not_alert(self):
        rows = [dict(ROW,bidPbancOrd='001'), dict(ROW,bidPbancNo='OTHER'), dict(ROW,bidPgstCd='개찰전')]
        session, context = self.session(rows)
        with patch('core.opening.requests.Session', return_value=context):
            self.assertIsNone(check_result({'bid_no':ROW['bidPbancNo'],'bid_seq':'000'}))
        self.assertEqual(session.get.call_count,1)

    def test_exact_identity_and_text_pagination(self):
        session, context = self.session([ROW])
        with patch('core.opening.requests.Session', return_value=context):
            result=check_result({'bid_no':ROW['bidPbancNo'],'bid_seq':'000'})
        self.assertEqual(result,RESULT)
        params=session.post.call_args.kwargs['json']['dlSrchOnbsRsltClsfListInM']
        self.assertTrue(all(isinstance(v,str) for v in params.values()))
        query=parse_qs(urlparse(result['url']).query)
        self.assertEqual(query['reBidNo'],['001'])
        self.assertEqual(query['pbancType'],['pbanc'])

    def test_failed_route_validation_never_alerts(self):
        session, context = self.session([ROW],'<title>시스템 접근 안내</title>')
        with patch('core.opening.requests.Session', return_value=context), self.assertRaises(ValueError):
            check_result({'bid_no':ROW['bidPbancNo'],'bid_seq':'000'})

    def test_missing_link_identity_and_nonpublic_states_rejected(self):
        for field in ('bidPbancNo','bidPbancOrd','bidClsfNo','bidPrgrsOrd','prcmBsneSeCd'):
            row=dict(ROW);row.pop(field)
            with self.assertRaises(ValueError): result_link(row)
        for status in ('개찰전','입찰진행','최종낙찰'):
            with self.assertRaises(ValueError): result_link(dict(ROW,bidPgstCd=status))

    def test_failed_and_rebid_are_opening_results(self):
        for status in ('유찰','재입찰'):
            self.assertIn('PBOC007_01',result_link(dict(ROW,bidPgstCd=status)))


if __name__=='__main__':
    unittest.main()
