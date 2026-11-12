import unittest
from unittest.mock import Mock
from requests import Response
from core.eiaa_collector import EiaaCollector

def response(text='', status=200, url=EiaaCollector.LIST_URL):
 r=Response(); r.status_code=status; r.url=url; r._content=text.encode(); r.encoding='utf-8'; return r

class AuthenticationTests(unittest.TestCase):
 def collector(self, responses):
  c=EiaaCollector('test-user','test-password'); c.session.request=Mock(side_effect=responses); return c
 def test_403_post_is_not_success_and_never_reaches_list(self):
  c=self.collector([response(),response('Forbidden',403)])
  with self.assertRaisesRegex(RuntimeError,'ACCESS_DENIED stage=login_post'): c.fetch_board_items()
  self.assertEqual(c.session.request.call_count,2)
 def test_cookie_does_not_skip_login(self):
  c=self.collector([response(),response(),response('<a href="logout.php">logout</a><table><tr><td>12</td><td><a href="?seq=12">환경영향평가 공고</a></td><td>2026-11-12</td></tr></table>')]); c.session.cookies.set('PHPSESSID','anonymous')
  self.assertEqual(len(c.fetch_board_items()),1)
  self.assertEqual([x.args[0] for x in c.session.request.call_args_list],['GET','POST','GET'])
 def test_rejected_login_is_redacted(self):
  c=self.collector([response(),response('alert("test-password incorrect")')])
  with self.assertRaisesRegex(RuntimeError,'AUTH_REJECTED') as cm: c.fetch_board_items()
  self.assertNotIn('test-password',str(cm.exception))
 def test_login_page_is_not_empty_success(self):
  c=self.collector([response(),response(),response('<form name="login_form">')])
  with self.assertRaisesRegex(RuntimeError,'AUTH_REQUIRED'): c.fetch_board_items()
 def test_shared_login_link_and_alert_script_are_not_rejection(self):
  c=self.collector([response(),response(),response('<a href="login.php">login</a><a href="logout.php">logout</a><script>function shared(){alert(1)}</script>등록된 게시물이 없습니다')])
  self.assertEqual(c.fetch_board_items(),[])
 def test_403_login_page_never_posts_credentials(self):
  c=self.collector([response('Forbidden',403)])
  with self.assertRaisesRegex(RuntimeError,'stage=login_page'): c.fetch_board_items()
  self.assertEqual(c.session.request.call_count,1)
 def test_unrecognized_board_fails(self):
  c=self.collector([response(),response(),response('logout maintenance')])
  with self.assertRaisesRegex(RuntimeError,'BOARD_UNRECOGNIZED'): c.fetch_board_items()
