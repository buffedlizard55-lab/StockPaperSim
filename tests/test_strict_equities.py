"""Synthetic fixtures test arithmetic only; NEVER used as published prices."""
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sim.strict_equities import EvidenceError, EvidenceGate, PaperLedger, SessionSchedule, digest
from sim.research_rules import evaluate
from scripts.build_site_desk import audit, build

ROOT = Path(__file__).resolve().parents[1]


class StrictEquitiesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = {'fixture': {'source_class': 'EXCHANGE', 'redistribution': 'APPROVED',
                         'rights_evidence_url': 'https://example.test/TEST-ONLY-rights',
                         'hosts': ['example.test'], 'receipts': {}}}
        self.gate = EvidenceGate(self.root, self.registry)
        self.season = {'id': 'test', 'start': '2026-09-21', 'end_exclusive': '2027-09-21'}
        self.schedule = SessionSchedule({day: {'open': day+'T13:30:00Z', 'close': day+'T20:00:00Z',
                        'settlement_date': following, 'source_url': 'https://example.test/TEST-calendar',
                        'settlement_source_url': 'https://example.test/TEST-settlement'}
                        for day,following in [('2026-09-21','2026-09-22'),('2026-09-22','2026-09-23')]})
        self.book = PaperLedger(str(self.root/'book.sqlite'), self.gate, self.schedule, self.season)
        self.addCleanup(self.book.close)

    def quote(self, id='q1', **changes):
        q = dict(feed_id='fixture', receipt_id=id, quote_id=id, symbol='ABC', bid='9.99', ask='10.00',
                 bid_size=10, ask_size=10, size_unit='shares', observed_at='2026-09-21T13:30:01Z',
                 currency='USD', status='TRADING', condition='REGULAR', instrument_type='STOCK', listing_exchange='XNAS')
        q.update(changes)
        p = self.root/(id+'.json')
        p.write_text(json.dumps({'quotes': [q]}))
        self.registry['fixture']['receipts'][q['receipt_id']] = {
            'path': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
            'url': 'https://example.test/TEST-quotes', 'received_at': q['observed_at']}
        return q

    def order(self, id='o1', **changes):
        o = dict(order_id=id, username='@TestOnly', strategy_version='fixture-v1', symbol='ABC', side='buy',
                 quantity=5, order_type='market', signal_at='2026-09-21T13:29:59Z',
                 signal_sha256='a'*64, signal_source_url='https://example.test/TEST-signal',
                 expires_at='2026-09-21T20:00:00Z', reason='TEST FIXTURE ONLY')
        o.update(changes)
        return o

    def submit(self, **changes):
        return self.book.submit(self.order(**changes), '2026-09-21T13:30:00Z')

    def execute(self, q=None, id='o1', now='2026-09-21T13:30:02Z'):
        return self.book.execute(id, q or self.quote(), now)

    def test_no_approved_feed_is_closed(self):
        q = self.quote(); self.registry.clear(); self.submit()
        with self.assertRaisesRegex(EvidenceError, 'NO_APPROVED_OFFICIAL_FEED'):
            self.execute(q)
        self.assertFalse(any(e['kind']=='FILL' for e in self.book.events()))

    def test_receipt_custody(self):
        q = self.quote(); self.submit()
        (self.root/'q1.json').write_text('{}')
        with self.assertRaisesRegex(EvidenceError, 'CHECKSUM'):
            self.execute(q)

    def test_unreadable_or_malformed_receipts_are_logged(self):
        self.submit();q=self.quote();(self.root/'q1.json').unlink()
        with self.assertRaisesRegex(EvidenceError,'RECEIPT_UNREADABLE'):self.execute(q)
        self.assertEqual(self.book.events()[-1]['kind'],'BLOCKED')
        for content,error in [('not json','INVALID_RECEIPT_JSON'), ('[]','INVALID_RECEIPT_SCHEMA')]:
            q=self.quote();p=self.root/'q1.json';p.write_text(content)
            self.registry['fixture']['receipts']['q1']['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
            with self.subTest(content=content),self.assertRaisesRegex(EvidenceError,error):self.execute(q)

    def test_normalized_quote_must_equal_receipt_row(self):
        q = self.quote(); q['ask']='11'; self.submit()
        with self.assertRaisesRegex(EvidenceError, 'NOT_IN_RECEIPT'):
            self.execute(q)

    def test_no_future_stale_naive_quotes(self):
        self.submit()
        for value,error in [('2026-09-21T13:30:03Z','FUTURE'),('2026-09-21T13:20:00Z','STALE'),
                            ('2026-09-21T13:30:01','TIMEZONE')]:
            with self.subTest(value=value):
                with self.assertRaisesRegex(EvidenceError,error):
                    self.execute(self.quote(observed_at=value))

    def test_invalid_quote_fields(self):
        self.submit()
        for field,value in [('ask','NaN'),('bid','Infinity'),('ask',True),('bid','10'),('ask_size',0),
                            ('bid_size',1.5),('size_unit','lots'),('condition','UNKNOWN'),
                            ('status','HALTED'),('instrument_type','INDEX'),('listing_exchange','OTHER')]:
            with self.subTest(field=field,value=value), self.assertRaises(EvidenceError):
                self.execute(self.quote(**{field:value}))

    def test_secondary_and_unlicensed_blocked(self):
        self.submit(); q=self.quote()
        for field,value in [('source_class','SECONDARY'),('redistribution','UNKNOWN'),('rights_evidence_url','')]:
            old=self.registry['fixture'][field];self.registry['fixture'][field]=value
            with self.subTest(field=field),self.assertRaises(EvidenceError):self.execute(q)
            self.registry['fixture'][field]=old

    def test_path_and_origin(self):
        self.submit();q=self.quote();r=self.registry['fixture']['receipts']['q1']
        r['url']='https://example.test.evil.org/x'
        with self.assertRaisesRegex(EvidenceError,'ORIGIN'):self.execute(q)
        r['path']='../outside.json'
        with self.assertRaisesRegex(EvidenceError,'PATH_ESCAPE'):self.execute(q)

    def test_order_recorded_before_quote(self):
        self.submit()
        with self.assertRaisesRegex(EvidenceError,'PRECEDES_ORDER'):
            self.execute(self.quote(observed_at='2026-09-21T13:30:00Z'))

    def test_buy_and_idempotent_retry(self):
        self.submit();q=self.quote();f=self.execute(q)
        self.assertEqual(f['quantity'],5);self.assertEqual(f['price'],'10.00')
        self.assertEqual(self.execute(q)['status'],'ALREADY_PROCESSED')
        self.assertEqual(self.book.account('@TestOnly','2026-09-21T13:30:02Z')['available_cash'],'99949.985')
        self.assertEqual(len([e for e in self.book.events() if e['kind']=='FILL']),1)

    def test_partial_capacity_shared_across_users(self):
        self.submit(quantity=8);self.submit(id='o2',username='@Other',quantity=8)
        q=self.quote();self.execute(q)
        result=self.execute(q,id='o2');self.assertEqual(result['status'],'PARTIAL');self.assertEqual(result['quantity'],2)
        self.assertEqual(sum(e['body']['quantity'] for e in self.book.events() if e['kind']=='FILL'),10)

    def test_distinct_quote_refills_remainder(self):
        self.submit(quantity=15);self.assertEqual(self.execute()['quantity'],10)
        result=self.execute(self.quote('q2',ask_size=15,observed_at='2026-09-21T13:30:03Z'),now='2026-09-21T13:30:04Z')
        self.assertEqual(result['quantity'],5)

    def test_same_quote_id_different_content_rejected(self):
        self.submit(quantity=8);self.submit(id='o2');self.execute(self.quote())
        with self.assertRaisesRegex(EvidenceError,'QUOTE_ID_REUSED'):
            self.execute(self.quote('q2',quote_id='q1'),id='o2')

    def test_new_quote_id_does_not_invent_replenished_size(self):
        self.submit(quantity=15);self.execute()
        q=self.quote('q2',observed_at='2026-09-21T13:30:03Z')
        self.assertEqual(self.execute(q,now='2026-09-21T13:30:04Z')['status'],
                         'BLOCKED_CAPACITY_OR_CASH_OR_HOLDINGS')
        self.assertEqual(self.book.orders()[0]['remaining_quantity'],5)

    def test_query_orders_blockers_and_unranked_users(self):
        self.submit();q=self.quote();self.registry['fixture']['redistribution']='UNKNOWN'
        with self.assertRaises(EvidenceError):self.execute(q)
        self.assertEqual(self.book.orders()[0]['last_blocker'],'REDISTRIBUTION_NOT_APPROVED')
        self.assertEqual(self.book.events()[-1]['kind'],'BLOCKED')
        self.assertEqual(self.book.leaderboard(['@TestOnly'],'2026-09-21T13:30:02Z')[0]['rank'],None)

    def test_top_return_ranking_and_missing_marks(self):
        self.submit();q=self.quote();self.execute(q)
        self.book.mark('@TestOnly',q,'2026-09-21T13:30:02Z')
        board=self.book.leaderboard(['@TestOnly','@NotTraded'],'2026-09-21T13:30:02Z')
        self.assertEqual(board[0]['rank'],1);self.assertIsNone(board[1]['rank'])
        self.assertTrue(all(r['rank'] is None for r in self.book.leaderboard(
            ['@TestOnly','@NotTraded'],'2026-09-21T13:30:09Z')))

    def test_limit_resting_does_not_fake_a_maker_fill(self):
        self.submit(order_type='limit',limit_price='9.99')
        self.assertEqual(self.execute()['status'],'RESTING_UNVERIFIED_QUEUE')
        self.assertEqual(self.book.events()[-1]['kind'],'EVALUATION')

    def test_limit_crosses_at_ask_not_limit(self):
        self.submit(order_type='limit',limit_price='11')
        self.assertEqual(self.execute()['price'],'10.00')

    def test_cash_cap(self):
        self.submit(quantity=10001)
        self.assertEqual(self.execute(self.quote(ask_size=20000))['quantity'],9997)

    def test_no_naked_short(self):
        self.submit(side='sell')
        self.assertEqual(self.execute()['status'],'BLOCKED_CAPACITY_OR_CASH_OR_HOLDINGS')

    def test_cancel_and_expiry(self):
        self.submit();self.book.cancel('o1','2026-09-21T13:30:01Z')
        self.assertEqual(self.execute()['status'],'CLOSED_ORDER')
        self.book.submit(self.order('o2',expires_at='2026-09-21T13:30:04Z'), '2026-09-21T13:30:03Z')
        self.assertEqual(self.execute(id='o2',now='2026-09-21T13:30:05Z')['status'],'EXPIRED')

    def test_fifo_pnl_and_t1_receivable(self):
        self.submit();self.execute()
        self.book.submit(self.order('sell',side='sell'), '2026-09-21T13:30:03Z')
        q=self.quote('q2',bid='12',ask='12.01',observed_at='2026-09-21T13:30:04Z')
        self.execute(q,id='sell',now='2026-09-21T13:30:05Z')
        a=self.book.account('@TestOnly','2026-09-21T13:30:05Z')
        self.assertEqual(a['realized_pnl'],'9.970');self.assertEqual(a['unsettled_sale_proceeds'],'59.985')
        self.assertEqual(a['equity'],'100009.970')
        self.assertEqual(self.book.settle('2026-09-22T21:00:00Z'),0)
        self.assertEqual(self.book.settle('2026-09-23T05:00:00Z'),2)
        self.assertEqual(self.book.settle('2026-09-23T05:00:00Z'),0)
        self.assertEqual(self.book.account('@TestOnly','2026-09-23T05:00:00Z')['available_cash'],'100009.970')

    def test_unknown_marks_are_not_zero_pnl(self):
        self.submit();q=self.quote();self.execute(q)
        a=self.book.account('@TestOnly','2026-09-21T13:30:02Z')
        self.assertIsNone(a['return_pct']);self.assertIsNone(a['equity'])
        self.book.mark('@TestOnly',q,'2026-09-21T13:30:02Z')
        a=self.book.account('@TestOnly','2026-09-21T13:30:03Z');self.assertIsNotNone(a['equity'])
        self.assertIsNone(self.book.account('@TestOnly','2026-09-21T13:30:10Z')['equity'])

    def test_old_quote_cannot_replace_newer_mark(self):
        self.submit();self.execute()
        newer=self.quote('new',observed_at='2026-09-21T13:30:04Z')
        self.book.mark('@TestOnly',newer,'2026-09-21T13:30:04Z')
        older=self.quote('old',observed_at='2026-09-21T13:30:03Z')
        with self.assertRaisesRegex(EvidenceError,'OUT_OF_ORDER_MARK'):
            self.book.mark('@TestOnly',older,'2026-09-21T13:30:05Z')

    def test_no_trade_not_measured(self):
        self.assertIsNone(self.book.account('@NeverTraded','2026-09-21T13:30:00Z')['return_pct'])

    def test_invalid_orders(self):
        for changes in [dict(quantity=0),dict(quantity=1.5),dict(quantity=True),dict(side='short'),
                        dict(order_type='stop'),dict(symbol='^SP500'),dict(signal_sha256='x'),
                        dict(signal_at='2026-09-21T13:31:00Z'),dict(order_type='limit',limit_price='NaN')]:
            with self.subTest(changes=changes),self.assertRaises(EvidenceError):self.submit(**changes)

    def test_idempotent_submission_and_conflict(self):
        self.assertTrue(self.submit());self.assertFalse(self.submit())
        with self.assertRaisesRegex(EvidenceError,'IDEMPOTENCY_CONFLICT'):self.submit(quantity=7)

    def test_session_no_weekday_guessing(self):
        self.submit();self.schedule.sessions.clear()
        with self.assertRaisesRegex(EvidenceError,'UNVERIFIED_SESSION'):self.execute()

    def test_early_close_and_dst(self):
        self.schedule.sessions['2026-11-27']={'open':'2026-11-27T14:30:00Z','close':'2026-11-27T18:00:00Z',
          'settlement_date':'2026-11-30','source_url':'https://example.test/test','settlement_source_url':'https://example.test/test'}
        self.assertEqual(self.schedule.at('2026-11-27T17:59:59Z')[0],'2026-11-27')
        with self.assertRaisesRegex(EvidenceError,'OUTSIDE_REGULAR'):self.schedule.at('2026-11-27T18:00:00Z')

    def test_missing_settlement_blocks(self):
        self.submit();del self.schedule.sessions['2026-09-21']['settlement_date']
        with self.assertRaisesRegex(EvidenceError,'SETTLEMENT_CALENDAR'):self.execute()

    def test_season_boundary_and_clock_rewind(self):
        with self.assertRaisesRegex(EvidenceError,'OUTSIDE_COMPETITION'):
            self.book.submit(self.order(),'2027-09-21T13:30:00Z')
        self.submit();self.execute()
        with self.assertRaisesRegex(EvidenceError,'CLOCK_REWIND'):self.submit(id='o2')
        with self.assertRaisesRegex(EvidenceError,'CLOCK_REWIND'):
            self.book.account('@TestOnly','2026-09-21T13:30:01Z')

    def test_restart_hash_and_immutable_config(self):
        self.submit();self.execute();expected=self.book.verify()
        reopened=PaperLedger(str(self.root/'book.sqlite'),self.gate,self.schedule,self.season)
        self.assertEqual(reopened.verify(),expected);reopened.close()
        with self.assertRaisesRegex(EvidenceError,'CONFIGURATION'):
            PaperLedger(str(self.root/'book.sqlite'),self.gate,self.schedule,self.season,cash='5')
        with self.assertRaises(sqlite3.IntegrityError):self.book.db.execute('DELETE FROM events')
        with self.assertRaises(sqlite3.IntegrityError):self.book.db.execute("UPDATE events SET kind='X'")
        self.book.export(self.root/'export.jsonl')
        self.assertEqual(len((self.root/'export.jsonl').read_text().splitlines()),len(self.book.events()))


class ResearchAndDeskTests(unittest.TestCase):
    def bars(self, prices):
        out=[]
        for i,p in enumerate(prices):
            at=(dt.datetime(2026,1,1,tzinfo=dt.timezone.utc)+dt.timedelta(days=i)).isoformat()
            out.append(dict(ended_at=at,available_at=at,received_at=at,close=p,high=p,low=p))
        return out

    def test_prototype_rules(self):
        specs=[('sma',{'fast':2,'slow':4}),('ema',{'fast':2,'slow':4}),('momentum',{'lookback':3}),
               ('donchian',{'lookback':3,'exit_window':2})]
        for mode,p in specs:
            s={'implementation':mode,'parameters':p}
            self.assertTrue(evaluate(s,self.bars([1,2,3,4,5]),'2026-02-01T00:00:00Z')['target_long'])
            self.assertFalse(evaluate(s,self.bars([5,4,3,2,1]),'2026-02-01T00:00:00Z')['target_long'])
            self.assertEqual(evaluate(s,[],'2026-02-01T00:00:00Z')['status'],'INSUFFICIENT_HISTORY')

    def test_rsi_and_constant_series(self):
        s={'implementation':'rsi','parameters':{'period':2,'entry':20,'exit':60}}
        self.assertTrue(evaluate(s,self.bars([5,4,3,2]),'2026-02-01T00:00:00Z')['target_long'])
        self.assertFalse(evaluate(s,self.bars([5,5,5,5]),'2026-02-01T00:00:00Z')['target_long'])
        self.assertTrue(evaluate(s,self.bars([5,5,5,5]),'2026-02-01T00:00:00Z',holding=True)['target_long'])

    def test_point_in_time_and_duplicate_rows(self):
        s={'implementation':'momentum','parameters':{'lookback':1}};bars=self.bars([1,2])
        for rows in [bars+bars, list(reversed(bars))]:
            with self.assertRaises(EvidenceError):evaluate(s,rows,'2026-02-01T00:00:00Z')
        bars[0]['received_at']='2026-03-01T00:00:00Z'
        with self.assertRaisesRegex(EvidenceError,'POINT_IN_TIME'):evaluate(s,bars,'2026-02-01T00:00:00Z')

    def test_registry_has_all_requested_topics_unique_names_and_no_returns(self):
        r=json.loads((ROOT/'research/strict/registry.json').read_text())
        self.assertEqual(r['approved_feeds'],{});self.assertGreaterEqual(len(r['strategies']),50)
        self.assertEqual(len(r['strategies']),len({s['username'] for s in r['strategies']}))
        used={x for s in r['strategies'] for x in s['source_ids']}
        self.assertTrue({'ceo','weather','insider','leap','nfl-injury','nba-injury','fda','ncaa','nfl','mlb','sports','gold','pine'}<=used)
        for s in r['strategies']:
            self.assertIsNone(s['results']);self.assertTrue(s['entry_rule']);self.assertTrue(s['exit_rule'])

    def test_audit_all_legacy_fills_and_intents(self):
        report=audit(ROOT/'memory')
        self.assertGreater(len(report['fills']),0);self.assertGreater(len(report['upcoming']),0)
        manifest=json.loads((ROOT/'memory/live'/report['run_id']/'manifest.json').read_text())
        self.assertEqual(len(report['fills']),manifest['counts']['fills'])
        self.assertEqual(report['strict_fills'],0);self.assertFalse(report['issues'])
        for f in report['fills']:
            self.assertIn('NO_OBSERVED_BID_ASK_SIZE_RECEIPT',f['blockers']);self.assertFalse(f['strict_eligible'])
        self.assertTrue(all(not x['execution_authorized'] for x in report['upcoming']))

    def test_deterministic_build(self):
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            files=build(ROOT/'memory',one);build(ROOT/'memory',two)
            for f in files:self.assertEqual((Path(one)/f).read_bytes(),(Path(two)/f).read_bytes())
            text=(Path(one)/'desk/index.html').read_text()
            self.assertIn('No official-price stock performance is claimed',text)
            self.assertNotIn('localStorage',text)
