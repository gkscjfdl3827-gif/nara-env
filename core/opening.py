"""First public opening result for delivered bids; no final-award polling.

Uses the same public G2B screen as a user, not the metered data.go.kr API.
Screen contract verified against PBOC006_01 / PBOC007_01 on 2026-11-17.
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode, quote
from pathlib import Path

import requests

KST = timezone(timedelta(hours=9))
BASE = 'https://www.g2b.go.kr'
LIST_PATH = '/pb/pbo/pboc/OnbsRslt/selectOnbsRsltClsfList.do'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36'
CATEGORIES = {'물품': '조070001', '일반용역': '조070002', '기술용역': '조070003',
              '용역': '조070003', '공사': '조070004', '기타': '조070005'}
STATUSES = {'개찰완료': '입160004', '유찰': '입160005', '재입찰': '입160006'}


def seq(value):
    value = str(value).strip()
    if not value.isdigit():
        raise ValueError('Missing or invalid bid sequence')
    return value.zfill(3)


def marker(no, order):
    return f'개찰키:{no}:{seq(order)}'


def result_link(row):
    # Flat pbancType=pbanc is explicitly supported by onpageload in PBOC007_01.
    required = ('bidPbancNo', 'bidPbancOrd', 'bidClsfNo', 'bidPrgrsOrd', 'prcmBsneSeCd')
    if any(row.get(k) is None or str(row[k]) == '' for k in required):
        raise ValueError('Incomplete opening link identity')
    if row.get('bidPgstCd') not in STATUSES:
        raise ValueError('Not a public opening result')
    params = {k: str(row[k]) for k in required}
    params.update(pbancType='pbanc', reBidNo=params.pop('bidPrgrsOrd'),
                  bidPgstCd=STATUSES[row['bidPgstCd']])
    return BASE + '/link/PBOC007_01/single/?' + urlencode(params)


def check_result(watch):
    """One exact-number query, bounded to 100 classifications; fail closed."""
    with requests.Session() as session:
        session.headers.update({'User-Agent': UA})
        session.get(BASE + '/', timeout=(2, 4)).raise_for_status()
        session.headers.update({
            'Menu-Info': json.dumps({'menuNo': '01522', 'menuCangVal': 'PBOC006_01',
                                    'bsneClsfCd': '%EC%97%85130028', 'scrnNo': '02117'}),
            'submissionid': 'mf_wfm_container_selectOnbsRsltClsfList',
            'Referer': BASE + '/'})
        # Text dataMap fields MUST be strings (numeric pagination yields HTTP 500).
        params = dict(bidPbancNo=watch['bid_no'], bidPbancOrd=watch['bid_seq'],
                      bidPbancNm='', onbsYmdStr='', onbsYmdEnd='',
                      prcmBsneSeCd='', frcpYn='Y', rsrvYn='Y', laseYn='Y',
                      chkBsneAllYn='Y', firstIndex='1', lastIndex='100', inqEnd='N',
                      stepBarYn='Y', pbancKndCd='', currentPage='1', recordCountPerPage='100')
        response = session.post(BASE + LIST_PATH,
                                json={'dlSrchOnbsRsltClsfListInM': params}, timeout=(2, 5))
        response.raise_for_status()
        data = response.json()
        if data.get('ErrorCode') != 0 or not isinstance(data.get('result', {}).get('onbsRsltClsfList'), list):
            raise ValueError('Invalid G2B opening response')
        for row in data['result']['onbsRsltClsfList']:
            if (row.get('bidPbancNo') != watch['bid_no'] or
                    seq(row.get('bidPbancOrd', '')) != watch['bid_seq'] or
                    row.get('bidPgstCd') not in STATUSES):
                continue
            url = result_link(row)
            # Validate actual G2B route and accepted identity, not an HTTP-200 error page.
            landing = session.get(url, timeout=(2, 4))
            landing.raise_for_status()
            if not re.search(r'"menuCangVal"\s*:\s*"PBOC007_01"', landing.text):
                raise ValueError('Opening direct route not accepted')
            return {'url': url, 'status': row['bidPgstCd'], 'row': row}
    return None


def init_watch(storage):
    with storage._get_conn() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS opening_watch (
            bid_no TEXT NOT NULL, bid_seq TEXT NOT NULL, notice_id TEXT NOT NULL,
            next_check REAL NOT NULL DEFAULT 0, result TEXT, notified INTEGER NOT NULL DEFAULT 0,
            last_error TEXT, suppressed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (bid_no, bid_seq))''')
        if 'suppressed' not in {r[1] for r in db.execute('PRAGMA table_info(opening_watch)')}:
            db.execute('ALTER TABLE opening_watch ADD COLUMN suppressed INTEGER NOT NULL DEFAULT 0')
        # notified=1 is the existing durable delivery acknowledgement. Never track
        # collected-only notices, plans, specifications, or title/keyword matches.
        rows = db.execute("SELECT unique_id,bid_no,bid_seq FROM notices WHERE stage='입찰공고' AND notified=1").fetchall()
        for row in rows:
            if row['bid_no'] and str(row['bid_seq']).isdigit():
                db.execute('INSERT OR IGNORE INTO opening_watch(bid_no,bid_seq,notice_id) VALUES (?,?,?)',
                           (row['bid_no'], seq(row['bid_seq']), row['unique_id']))
        # Explicit live-observed public results, never inferred from a schedule.
        baseline_path = Path(__file__).with_name('opening_baseline.json')
        if baseline_path.exists():
            baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
            for no, order in baseline['public_keys']:
                db.execute('UPDATE opening_watch SET suppressed=1,result=NULL,last_error=? '
                           'WHERE bid_no=? AND bid_seq=? AND notified=0',
                           ('already-public-at-migration', no, order))
        # Old scheduled retry times must not delay a newly public result.
        db.execute('CREATE TABLE IF NOT EXISTS opening_meta(key TEXT PRIMARY KEY,value TEXT)')
        if not db.execute("SELECT 1 FROM opening_meta WHERE key='publication-v2'").fetchone():
            # An old runner may have suppressed a newly published delayed result
            # while this change was being deployed. Retain only the observed baseline.
            db.execute("UPDATE opening_watch SET suppressed=0,last_error=NULL WHERE notified=0 AND last_error LIKE 'historical-before-%'")
            if baseline_path.exists():
                for no, order in baseline['public_keys']:
                    db.execute('UPDATE opening_watch SET suppressed=1,last_error=? WHERE bid_no=? AND bid_seq=? AND notified=0',
                               ('already-public-at-migration',no,order))
            db.execute('UPDATE opening_watch SET next_check=0 WHERE notified=0 AND suppressed=0')
            db.execute("INSERT INTO opening_meta VALUES('publication-v2','1')")
        db.commit()


def recent_public_keys():
    """Cheap discovery across existing search keywords; exact checks remain authoritative.

    A seven-day overlapping discovery window finds delayed publications even when
    their scheduled dates are older. Exact round-robin polling covers omissions,
    older records, pagination overflow, and keyword changes.
    """
    keys = set()
    now = datetime.now(KST)
    with requests.Session() as session:
        session.headers.update({'User-Agent': UA})
        session.get(BASE + '/', timeout=(2, 4)).raise_for_status()
        session.headers.update({'Menu-Info': json.dumps({'menuNo':'01522', 'menuCangVal':'PBOC006_01',
            'bsneClsfCd':'%EC%97%85130028','scrnNo':'02117'}), 'Referer': BASE + '/'})
        for keyword in ('환경영향', '전략환경', '소규모환경', '사후환경'):
            params = dict(bidPbancNo='',bidPbancOrd='',bidPbancNm=keyword,
                onbsYmdStr=(now-timedelta(days=6)).strftime('%Y%m%d'),onbsYmdEnd=now.strftime('%Y%m%d'),
                prcmBsneSeCd='',frcpYn='Y',rsrvYn='Y',laseYn='Y',chkBsneAllYn='Y',
                firstIndex='1',lastIndex='100',inqEnd='N',stepBarYn='',pbancKndCd='',
                currentPage='1',recordCountPerPage='100')
            response = session.post(BASE + LIST_PATH,json={'dlSrchOnbsRsltClsfListInM':params},timeout=(2,4))
            response.raise_for_status()
            data=response.json()
            if data.get('ErrorCode') != 0 or not isinstance(data.get('result',{}).get('onbsRsltClsfList'),list):
                raise ValueError('Invalid public discovery response')
            for row in data['result']['onbsRsltClsfList']:
                if row.get('bidPgstCd') in STATUSES and row.get('bidPbancNo'):
                    keys.add((row['bidPbancNo'],seq(row['bidPbancOrd'])))
    return keys


def run_openings(storage, notifier, topic, limit=8, budget=25):
    init_watch(storage)
    if not topic:
        return False
    started = time.monotonic()
    now = time.time()
    try:
        public_keys = recent_public_keys()
    except Exception as exc:
        print('[Opening] Discovery deferred; exact polling continues:',type(exc).__name__)
        public_keys = set()
    with storage._get_conn() as db:
        rows = [dict(r) for r in db.execute('''SELECT w.*,n.title,n.category,n.order_agency,n.close_date
            FROM opening_watch w JOIN notices n ON n.unique_id=w.notice_id
            WHERE w.notified=0 AND w.suppressed=0 AND n.notified=1
            ORDER BY (w.result IS NOT NULL) DESC,w.next_check,w.bid_no''')]
    candidates = [r for r in rows if r['next_check'] <= now or (r['bid_no'],r['bid_seq']) in public_keys]
    candidates.sort(key=lambda r:(not bool(r['result']), (r['bid_no'],r['bid_seq']) not in public_keys,r['next_check']))
    due = candidates[:limit]
    if not due:
        print('[Opening] No due delivered bids')
        return False
    # Reconcile accepted pushes after timeout / killed runner / failed DB push.
    # If receipt history is unavailable, fail closed instead of risking duplicates.
    receipts = set()
    try:
        response = requests.get(f'https://ntfy.sh/{quote(topic, safe="")}/json',
                                params={'poll': '1', 'since': 'all'}, timeout=(2, 5))
        response.raise_for_status()
        for line in response.text.splitlines():
            item = json.loads(line)
            if item.get('event') == 'message':
                receipts.update(re.findall(r'개찰키:[A-Za-z0-9]+:\d{3}', item.get('message', '')))
    except (requests.RequestException, ValueError):
        print('[Opening] Receipt lookup failed; retry next run (no send)')
        return True
    failed = False
    # Eight exact bid queries, at most four simultaneous; no national result scans.
    def fetch(row):
        try:
            return row, json.loads(row['result']) if row['result'] else check_result(row), None
        except Exception as exc:
            return row, None, type(exc).__name__  # Never log request URLs/secrets.
    with ThreadPoolExecutor(max_workers=4) as pool:
        for row, result, error in pool.map(fetch, due):
            identity = (row['bid_no'], row['bid_seq'])
            event = marker(*identity)
            interval = 120
            with storage._get_conn() as db:
                db.execute('UPDATE opening_watch SET next_check=?,last_error=?,result=COALESCE(?,result) WHERE bid_no=? AND bid_seq=?',
                           (now + interval, error, json.dumps(result, ensure_ascii=False) if result else None, *identity))
                if event in receipts:
                    db.execute('UPDATE opening_watch SET notified=1 WHERE bid_no=? AND bid_seq=?', identity)
                db.commit()
            if event in receipts:
                print('[Opening] Reconciled receipt:', *identity)
                continue
            if error:
                failed = True
                print('[Opening] Query deferred:', *identity, error)
                continue
            if not result or time.monotonic() - started >= budget:
                continue
            # Cached result survives failed sends and no longer needs G2B polling.
            if notifier.send_opening(row, result, event):
                receipts.add(event)
                with storage._get_conn() as db:
                    db.execute('UPDATE opening_watch SET notified=1 WHERE bid_no=? AND bid_seq=?', identity)
                    db.commit()
                print('[Opening] Delivered:', *identity, result['status'])
            else:
                failed = True
                print('[Opening] Delivery deferred:', *identity)
    print(f'[Opening] Checked {len(due)} delivered bids in {time.monotonic()-started:.1f}s')
    return failed
