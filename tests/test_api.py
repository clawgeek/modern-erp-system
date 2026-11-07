# -*- coding: utf-8 -*-
"""云枢 ERP 真实业务冒烟测试：起真实服务、打真实接口、验真实数据库副作用。

    python tests/test_api.py

覆盖：健康检查 / 驾驶舱聚合 / AI 推荐 / 订单创建幂等 / 收货原子事务 /
      凭证硬规则过账 / Copilot 编排 / 审计留痕。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8799
BASE = 'http://127.0.0.1:%d' % PORT


def call(path, body=None, headers=None):
    url = BASE + path
    data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
    req = urllib.request.Request(url, data=data, method='POST' if body is not None else 'GET',
                                 headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode('utf-8'))


class ErpApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='erp-test-')
        env = dict(os.environ)
        env['ERP_DB_PATH'] = os.path.join(cls.tmp, 'test.db')
        env['PYTHONUNBUFFERED'] = '1'
        cls.proc = subprocess.Popen([sys.executable, 'server.py', '--port', str(PORT), '--reset'],
                                    cwd=ROOT, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for _ in range(60):
            try:
                s, d = call('/api/health')
                if s == 200 and d['status'] == 'UP':
                    return
            except Exception:
                pass
            time.sleep(0.25)
        out = ''
        try:
            out = cls.proc.communicate(timeout=2)[0]
        except Exception:
            pass
        raise RuntimeError('服务启动失败:\n' + (out or ''))

    @classmethod
    def tearDownClass(cls):
        try:
            cls.proc.terminate()
            cls.proc.wait(timeout=5)
        except Exception:
            cls.proc.kill()

    def test_01_health_and_seed(self):
        s, d = call('/api/health')
        self.assertEqual(s, 200)
        self.assertEqual(d['status'], 'UP')
        self.assertGreaterEqual(d['tables'], 12)
        s, mats = call('/api/materials')
        self.assertEqual(len(mats), 12)
        s, sups = call('/api/suppliers')
        self.assertEqual(len(sups), 8)

    def test_02_dashboard_real_aggregation(self):
        s, d = call('/api/dashboard')
        self.assertEqual(s, 200)
        self.assertIn('¥', d['kpi']['order_amount'])
        self.assertEqual(len(d['series']), 12)
        self.assertGreater(len(d['regions']), 0)

    def test_03_ai_recommendation(self):
        s, d = call('/api/ai/recommend', {'sku': 'A1024', 'qty': 760})
        self.assertEqual(s, 200)
        self.assertEqual(len(d['recommendations']), 3)
        self.assertGreaterEqual(d['recommendations'][0]['score'], d['recommendations'][1]['score'])
        self.assertIn('suppliers', d['data_sources'])

    def test_04_order_idempotency(self):
        body = {'sku': 'A1024', 'qty': 760, 'supplier_id': 'SUP-01'}
        h = {'Idempotency-Key': 'test-order-001'}
        s1, a = call('/api/orders', body, h)
        s2, b = call('/api/orders', body, h)
        self.assertEqual(s1, 200)
        self.assertEqual(a['order_id'], b['order_id'])
        self.assertEqual(a['status'], 'CONFIRMED')
        s3, bad = call('/api/orders', {'sku': 'NOPE', 'qty': 1, 'supplier_id': 'SUP-01'})
        self.assertEqual(s3, 400)
        self.__class__.ORDER = a['order_id']

    def test_05_receive_atomic_transaction(self):
        oid = getattr(self.__class__, 'ORDER', None) or 'PO-SEED'
        s, inv0 = call('/api/inventory')
        before = next(r['qty'] for r in inv0 if r['sku'] == 'A1024')
        s, d = call('/api/orders/' + oid + '/receive', {'idempotency_key': 'test-recv-001'})
        self.assertEqual(s, 200)
        self.assertTrue(d['atomic_transaction'])
        self.assertEqual(d['inventory_after'] - d['inventory_before'], 760)
        s, inv1 = call('/api/inventory')
        after = next(r['qty'] for r in inv1 if r['sku'] == 'A1024')
        self.assertEqual(after - before, 760)
        s, dup = call('/api/orders/' + oid + '/receive', {})
        self.assertEqual(s, 409)
        self.__class__.VOUCHER = d['voucher_draft']

    def test_06_voucher_hard_rule_and_post(self):
        s, d = call('/api/ai/voucher-suggest', {'memo': '发布会会务费', 'amount_cents': 8640000})
        self.assertEqual(s, 200)
        self.assertEqual(d['status'], 'DRAFT')
        vno = getattr(self.__class__, 'VOUCHER', None) or d['voucher_no']
        s, posted = call('/api/vouchers/' + vno + '/approve', {})
        self.assertEqual(s, 200)
        self.assertEqual(posted['status'], 'POSTED')
        s, dup = call('/api/vouchers/' + vno + '/approve', {})
        self.assertEqual(s, 409)
        s, bad = call('/api/vouchers/NOT-EXIST/approve', {})
        self.assertEqual(s, 404)

    def test_07_copilot_and_audit(self):
        s, d = call('/api/copilot', {'message': '查询低于安全水位的库存并生成补货任务'})
        self.assertEqual(s, 200)
        self.assertGreater(len(d['tools']), 0)
        self.assertTrue(d['needs_approval'])
        s, logs = call('/api/audit')
        actions = [r['action'] for r in logs]
        for a in ('ORDER_CREATE', 'ORDER_RECEIVE', 'VOUCHER_POST', 'COPILOT_QUERY'):
            self.assertIn(a, actions)


if __name__ == '__main__':
    unittest.main(verbosity=2)
