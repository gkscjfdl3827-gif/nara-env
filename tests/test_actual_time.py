import unittest
from unittest.mock import Mock
from core.opening import actual_result_time

ROW=dict(bidPbancNo='R26BK01823336',bidPbancOrd='000',bidClsfNo='0',
         bidPrgrsOrd='000',prcmBsneSeCd='05',bidPgstCd='개찰완료')

class ActualTimeTests(unittest.TestCase):
    def detail(self, info, extra=None):
        s=Mock();s.headers={}
        s.post.return_value.json.return_value={'ErrorCode':0,'result':{'bidInfo':info,'etcData':extra or {}}}
        return s

    def test_actual_time_wins_over_planned_time(self):
        s=self.detail(dict(ROW,realOnbsDt='2026/11/17 13:25:23',onbsPrnmntDt='2026/11/17 11:00:00'))
        self.assertEqual(actual_result_time(s,ROW),'2026-11-17 13:25:23')

    def test_planned_time_is_never_fallback(self):
        s=self.detail(dict(ROW,onbsPrnmntDt='2026/11/17 11:00:00'))
        self.assertIsNone(actual_result_time(s,ROW))

    def test_official_second_actual_field(self):
        s=self.detail(ROW,{'bidOpenDt':'2026/11/17 13:25:23'})
        self.assertEqual(actual_result_time(s,ROW),'2026-11-17 13:25:23')

    def test_other_notice_rejected(self):
        s=self.detail(dict(ROW,bidPbancNo='OTHER',realOnbsDt='2026/11/17 13:25:23'))
        with self.assertRaises(ValueError):actual_result_time(s,ROW)

    def test_failure_is_retryable(self):
        s=self.detail(ROW);s.post.return_value.json.return_value={'ErrorCode':-1}
        with self.assertRaises(ValueError):actual_result_time(s,ROW)
