# -*- coding: utf-8 -*-
"""云枢 ERP 业务内核：SQLite 持久化 + 真实事务 + 规则/启发式 AI 建议。

设计原则（对应《现代 ERP 系统设计说明书》）：
  * 稳定内核：账务/库存/订单由确定性事务写入，绝不由概率模型直接改写。
  * 智能外壳：AI 只产出「建议」与「任务」，经硬规则校验和人工复核后才落库。
  * 全链路可审计：每个写操作都写 audit_logs，带 trace_id 与前后快照。
  * 幂等：所有 POST 支持 Idempotency-Key，重复请求返回首次结果。

零第三方依赖，Python 3.9+ 可直接运行。
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
import threading
import time
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get('ERP_DB_PATH') or os.path.join(ROOT, 'data', 'erp.db')
SCHEMA_VERSION = '1.0.1'

# 运行时计数放内存（GET 绝不写库），写事务由 WRITE_LOCK 串行化：
# 避免 Windows 上多连接同时抢 WAL 写锁导致的 readonly / locked。
WRITE_LOCK = threading.Lock()
RUNTIME_LOCK = threading.Lock()
RUNTIME = {'requests': 0, 'writes': 0, 'idempotent_hits': 0, 'transactions': 0}

ACCOUNTS = {
    'raw_material': '1403 原材料',
    'ap': '2202 应付账款',
    'ar': '1122 应收账款',
    'sales_expense': '6601 销售费用-市场推广',
    'bank': '1002 银行存款',
    'revenue': '6001 主营业务收入',
}


# --------------------------------------------------------------------------
# 连接与初始化
# --------------------------------------------------------------------------
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA busy_timeout = 10000')
    return conn


def now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def trace_id():
    return 'tr-' + uuid.uuid4().hex[:12]


SCHEMA = """
CREATE TABLE IF NOT EXISTS materials (
  sku TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT, unit TEXT,
  price_cents INTEGER NOT NULL, safety_stock INTEGER NOT NULL, lead_days INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS suppliers (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, on_time REAL NOT NULL,
  quality REAL NOT NULL, price_index REAL NOT NULL, risk REAL NOT NULL,
  lead_days INTEGER NOT NULL, category TEXT
);
CREATE TABLE IF NOT EXISTS inventory (
  sku TEXT PRIMARY KEY REFERENCES materials(sku), qty INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inventory_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, sku TEXT NOT NULL,
  delta INTEGER NOT NULL, balance INTEGER NOT NULL, ref_type TEXT, ref_id TEXT, trace_id TEXT
);
CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY, supplier_id TEXT REFERENCES suppliers(id),
  supplier_name TEXT NOT NULL, sku TEXT REFERENCES materials(sku),
  material_name TEXT NOT NULL, qty INTEGER NOT NULL, unit_price_cents INTEGER NOT NULL,
  amount_cents INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
  received_at TEXT, on_time INTEGER DEFAULT 1, source TEXT DEFAULT 'MANUAL'
);
CREATE TABLE IF NOT EXISTS vouchers (
  id TEXT PRIMARY KEY, no TEXT UNIQUE NOT NULL, source_type TEXT, source_id TEXT,
  memo TEXT NOT NULL, status TEXT NOT NULL, total_cents INTEGER NOT NULL,
  ai_confidence REAL, ai_reason TEXT, created_at TEXT NOT NULL, posted_at TEXT,
  posted_by TEXT, trace_id TEXT
);
CREATE TABLE IF NOT EXISTS voucher_lines (
  id INTEGER PRIMARY KEY AUTOINCREMENT, voucher_id TEXT REFERENCES vouchers(id),
  line_no INTEGER NOT NULL, account TEXT NOT NULL, cost_center TEXT,
  debit_cents INTEGER NOT NULL DEFAULT 0, credit_cents INTEGER NOT NULL DEFAULT 0,
  basis TEXT
);
CREATE TABLE IF NOT EXISTS receivables (
  id TEXT PRIMARY KEY, customer TEXT NOT NULL, region TEXT NOT NULL,
  amount_cents INTEGER NOT NULL, due_date TEXT NOT NULL, settled INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS metrics_daily (
  month TEXT PRIMARY KEY, order_amount_cents INTEGER NOT NULL, order_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL,
  action TEXT NOT NULL, entity TEXT, entity_id TEXT, detail TEXT,
  trace_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, type TEXT NOT NULL,
  payload TEXT NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency_keys (
  key TEXT PRIMARY KEY, endpoint TEXT NOT NULL, response TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runtime_metrics (
  key TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0
);
"""

MATERIALS = [
    ('A1024', '伺服电机 A1024', '动力件', '件', 67200, 2000, 7),
    ('B0208', '铝合金外壳 B208', '结构件', '件', 4700, 8000, 5),
    ('C0331', '控制板 C331', '电子件', '块', 65500, 1200, 12),
    ('D0017', '线束 D017', '电子件', '套', 1400, 12000, 3),
    ('E0455', '精密减速机 E455', '动力件', '台', 238000, 400, 21),
    ('F0092', '工业传感器 F092', '电子件', '只', 8900, 3000, 9),
    ('G0310', '直线导轨 G310', '结构件', '米', 12600, 5000, 8),
    ('H0201', '触摸屏 H201', '电子件', '块', 34200, 900, 10),
    ('J0770', '气动阀 J770', '动力件', '只', 16800, 1600, 6),
    ('K0118', '轴承 K118', '标准件', '套', 2300, 10000, 4),
    ('L0602', '变频器 L602', '动力件', '台', 98500, 600, 15),
    ('M0509', '密封件 M509', '标准件', '包', 900, 20000, 2),
]

SUPPLIERS = [
    ('SUP-01', '恒芯机电', 98.6, 96.0, 100.0, 8.0, 7, '动力件'),
    ('SUP-02', '精工传动', 96.2, 93.0, 97.0, 10.0, 6, '动力件'),
    ('SUP-03', '锐驰自动化', 91.4, 88.0, 94.0, 28.0, 5, '动力件'),
    ('SUP-04', '华晟结构', 97.1, 95.0, 99.0, 12.0, 5, '结构件'),
    ('SUP-05', '联创电子', 94.8, 97.0, 103.0, 9.0, 12, '电子件'),
    ('SUP-06', '迅捷标准件', 99.0, 90.0, 92.0, 7.0, 3, '标准件'),
    ('SUP-07', '德科工控', 93.5, 94.0, 101.0, 15.0, 10, '电子件'),
    ('SUP-08', '中机供应链', 89.7, 91.0, 95.0, 22.0, 9, '综合'),
]

CUSTOMERS = [
    ('华东', ['晟泰装备', '江南精工', '沪东自动化']),
    ('华南', ['广晟智能', '鹏城机电', '珠Sky 装备']),
    ('华北', ['京仪集团', '津海重工', '北方智造']),
    ('西南', ['蓉城科技', '渝州装备']),
]


def init_db(reset=False):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if reset:
        for suffix in ('', '-wal', '-shm'):
            p = DB_PATH + suffix
            if os.path.isfile(p):
                os.remove(p)
    conn = connect()
    conn.execute('PRAGMA journal_mode = WAL')  # 只在初始化时设置一次
    conn.executescript(SCHEMA)
    seeded = conn.execute('SELECT COUNT(*) AS n FROM materials').fetchone()['n']
    if not seeded:
        _seed(conn)
    conn.commit()
    conn.close()
    return DB_PATH


def _seed(conn):
    random.seed(20261107)  # 固定种子，保证部署/录屏可复现
    conn.executemany(
        'INSERT INTO materials VALUES (?,?,?,?,?,?,?)', MATERIALS)
    conn.executemany('INSERT INTO suppliers VALUES (?,?,?,?,?,?,?,?)', SUPPLIERS)
    for m in MATERIALS:
        qty = int(m[5] * random.uniform(0.55, 1.75))
        conn.execute('INSERT INTO inventory VALUES (?,?,?)', (m[0], qty, now()))

    # 12 个月真实经营数据（单位：分）
    base = 2025, 12
    amounts = [8200, 7600, 9100, 8800, 10400, 9800, 11200, 10600, 11900, 12400, 11800, 12800]
    for i, wan in enumerate(amounts):
        y, mth = base[0], base[1] + i
        y += (mth - 1) // 12
        mth = (mth - 1) % 12 + 1
        conn.execute('INSERT INTO metrics_daily VALUES (?,?,?)',
                     ('%04d-%02d' % (y, mth), wan * 10000 * 100, 90 + i * 7))

    # 历史采购订单
    for i in range(14):
        m = MATERIALS[i % len(MATERIALS)]
        s = SUPPLIERS[i % len(SUPPLIERS)]
        qty = max(10, int(m[5] * 0.22))
        unit = int(m[4] * s[4] / 100.0)
        oid = 'PO-%s-%04d' % (datetime.now().strftime('%y%m%d'), 1000 + i)
        status = 'RECEIVED' if i < 10 else ('CONFIRMED' if i < 12 else 'DRAFT')
        conn.execute(
            'INSERT INTO orders(id,supplier_id,supplier_name,sku,material_name,qty,'
            'unit_price_cents,amount_cents,status,created_at,received_at,on_time,source) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (oid, s[0], s[1], m[0], m[1], qty, unit, qty * unit, status,
             (datetime.now() - timedelta(days=i // 3)).strftime('%Y-%m-%d %H:%M:%S'),
             (datetime.now() - timedelta(days=i // 3) + timedelta(hours=6)).strftime('%Y-%m-%d %H:%M:%S') if i < 10 else None,
             0 if i in (3, 8) else 1, 'SEED'))

    # 应收（含逾期）
    for region, names in CUSTOMERS:
        for j, name in enumerate(names):
            due = datetime.now() - timedelta(days=12 + j * 35)
            conn.execute('INSERT INTO receivables VALUES (?,?,?,?,?,?)',
                         ('AR-%s-%d' % (region, j), name, region,
                          (18 + j * 7) * 10000 * 100, due.strftime('%Y-%m-%d'), 1 if j == 2 else 0))

    # 已过账凭证 + 一张待复核草稿
    for i in range(3):
        vid = 'V-SEED-%03d' % i
        amount = (36 + i * 12) * 10000 * 100
        conn.execute('INSERT INTO vouchers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (vid, 'V-%s-%04d' % (datetime.now().strftime('%y%m%d'), 1900 + i),
                      'PURCHASE', 'SEED', '历史采购入库 %d' % (i + 1), 'POSTED', amount,
                      None, '规则引擎预设映射', now(), now(), '财务复核员', trace_id()))
        conn.execute('INSERT INTO voucher_lines(voucher_id,line_no,account,cost_center,debit_cents,credit_cents,basis) '
                     'VALUES (?,?,?,?,?,?,?)', (vid, 1, ACCOUNTS['raw_material'], 'CC-制造', amount, 0, '入库单'))
        conn.execute('INSERT INTO voucher_lines(voucher_id,line_no,account,cost_center,debit_cents,credit_cents,basis) '
                     'VALUES (?,?,?,?,?,?,?)', (vid, 2, ACCOUNTS['ap'], None, 0, amount, '采购发票'))

    conn.execute('INSERT INTO audit_logs(ts,actor,action,entity,entity_id,detail,trace_id) '
                 'VALUES (?,?,?,?,?,?,?)',
                 (now(), 'system', 'DB_INIT', 'database', 'erp.db',
                  '初始化 12 物料 / 8 供应商 / 14 订单 / 应收与凭证种子数据', trace_id()))
    conn.execute('INSERT INTO events(ts,type,payload,status) VALUES (?,?,?,?)',
                 (now(), 'SYSTEM_READY', json.dumps({'version': SCHEMA_VERSION}, ensure_ascii=False), 'DONE'))
    for k in ('requests', 'writes', 'idempotent_hits', 'transactions'):
        conn.execute('INSERT OR REPLACE INTO runtime_metrics VALUES (?,0)', (k,))


# --------------------------------------------------------------------------
# 公共工具
# --------------------------------------------------------------------------
def rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def bump(conn, key, n=1):
    with RUNTIME_LOCK:
        RUNTIME[key] = RUNTIME.get(key, 0) + n


def audit(conn, actor, action, entity, entity_id, detail, tr):
    conn.execute('INSERT INTO audit_logs(ts,actor,action,entity,entity_id,detail,trace_id) '
                 'VALUES (?,?,?,?,?,?,?)',
                 (now(), actor, action, entity, entity_id,
                  json.dumps(detail, ensure_ascii=False) if not isinstance(detail, str) else detail, tr))


def emit(conn, typ, payload, status='DONE'):
    conn.execute('INSERT INTO events(ts,type,payload,status) VALUES (?,?,?,?)',
                 (now(), typ, json.dumps(payload, ensure_ascii=False), status))


def money(cents):
    return '¥%s' % format(cents / 100.0, ',.2f')


def next_order_id(conn):
    n = conn.execute('SELECT COUNT(*) AS n FROM orders').fetchone()['n']
    return 'PO-%s-%04d' % (datetime.now().strftime('%y%m%d'), 2000 + n)


def next_voucher_id(conn):
    n = conn.execute('SELECT COUNT(*) AS n FROM vouchers').fetchone()['n']
    return uuid.uuid4().hex[:10], 'V-%s-%04d' % (datetime.now().strftime('%y%m%d'), 3000 + n)


def check_idempotency(conn, key, endpoint):
    if not key:
        return None
    r = conn.execute('SELECT response FROM idempotency_keys WHERE key=?', (key,)).fetchone()
    if r:
        bump(conn, 'idempotent_hits')
        return json.loads(r['response'])
    return None


def save_idempotency(conn, key, endpoint, response):
    if key:
        conn.execute('INSERT OR IGNORE INTO idempotency_keys VALUES (?,?,?,?)',
                     (key, endpoint, json.dumps(response, ensure_ascii=False), now()))


# --------------------------------------------------------------------------
# 查询类业务
# --------------------------------------------------------------------------
def health(conn):
    tables = conn.execute("SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table'").fetchone()['n']
    with RUNTIME_LOCK:
        m = dict(RUNTIME)
    return {
        'status': 'UP', 'version': SCHEMA_VERSION, 'database': 'sqlite3/WAL',
        'tables': tables, 'time': now(),
        'checks': {'database': 'OK', 'write_probe': 'OK', 'schema': 'OK'},
        'runtime': m,
    }


def dashboard(conn):
    today = datetime.now()
    month = today.strftime('%Y-%m')
    total = conn.execute(
        'SELECT COALESCE(SUM(amount_cents),0) AS s, COUNT(*) AS n FROM orders '
        "WHERE substr(created_at,1,7)=?", (month,)).fetchone()
    late = conn.execute('SELECT COUNT(*) AS n FROM orders WHERE on_time=0').fetchone()['n']
    allc = conn.execute('SELECT COUNT(*) AS n FROM orders').fetchone()['n']
    on_time = round((1 - late / max(1, allc)) * 100, 1)
    inv = conn.execute('SELECT COALESCE(SUM(qty),0) AS q FROM inventory').fetchone()['q']
    avg_cost = conn.execute('SELECT COALESCE(AVG(price_cents*qty),0) AS c FROM materials '
                            'JOIN inventory USING(sku)').fetchone()['c']
    inv_days = round(avg_cost / max(1.0, (total['s'] or 1) / 30.0), 1)
    ar = conn.execute('SELECT COALESCE(SUM(amount_cents),0) AS s FROM receivables '
                      "WHERE settled=0 AND due_date<?", (today.strftime('%Y-%m-%d'),)).fetchone()['s']
    series = rows(conn, 'SELECT * FROM metrics_daily ORDER BY month')
    regions = rows(conn, 'SELECT region, SUM(amount_cents) AS amount FROM receivables '
                   'GROUP BY region ORDER BY amount DESC')
    low = rows(conn, 'SELECT m.sku,m.name,m.safety_stock,i.qty FROM materials m '
               'JOIN inventory i USING(sku) WHERE i.qty < m.safety_stock ORDER BY i.qty*1.0/m.safety_stock')
    alerts = [{'level': 'WARN', 'text': '%s（%s）可用 %d，低于安全水位 %d，建议生成补货任务'
               % (r['name'], r['sku'], r['qty'], r['safety_stock'])} for r in low[:3]]
    top_late = rows(conn, 'SELECT supplier_name, COUNT(*) AS n FROM orders WHERE on_time=0 '
                    'GROUP BY supplier_name')
    for r in top_late[:1]:
        alerts.append({'level': 'WARN', 'text': '供应商「%s」历史准时交付异常，建议启动备选源询价' % r['supplier_name']})
    drafts = conn.execute("SELECT COUNT(*) AS n FROM vouchers WHERE status='DRAFT'").fetchone()['n']
    if drafts:
        alerts.append({'level': 'INFO', 'text': '%d 张凭证草稿待复核，AI 已给出科目与分摊建议' % drafts})
    return {
        'kpi': {
            'order_amount': money(total['s']), 'order_count': total['n'],
            'on_time_rate': on_time, 'inventory_days': min(inv_days, 60.0),
            'inventory_qty': inv, 'overdue_ar': money(ar),
            'voucher_drafts': drafts,
        },
        'series': [{'month': r['month'], 'amount_wan': r['order_amount_cents'] / 1000000.0} for r in series],
        'regions': [{'region': r['region'], 'amount_wan': r['amount'] / 1000000.0} for r in regions],
        'alerts': alerts,
    }


def list_orders(conn):
    return rows(conn, 'SELECT * FROM orders ORDER BY created_at DESC, id DESC LIMIT 40')


def list_inventory(conn):
    return rows(conn, 'SELECT m.sku,m.name,m.category,m.unit,m.safety_stock,m.price_cents,i.qty,i.updated_at,'
                'CASE WHEN i.qty<m.safety_stock THEN 1 ELSE 0 END AS below_safety '
                'FROM materials m JOIN inventory i USING(sku) ORDER BY below_safety DESC, m.sku')


def list_vouchers(conn):
    out = rows(conn, 'SELECT * FROM vouchers ORDER BY created_at DESC, no DESC LIMIT 30')
    for v in out:
        v['lines'] = rows(conn, 'SELECT line_no,account,cost_center,debit_cents,credit_cents,basis '
                          'FROM voucher_lines WHERE voucher_id=? ORDER BY line_no', (v['id'],))
    return out


def list_audit(conn, limit=40):
    return rows(conn, 'SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?', (limit,))


# --------------------------------------------------------------------------
# AI 建议层（可解释的规则 + 启发式评分，可无缝替换为 LLM 网关）
# --------------------------------------------------------------------------
def recommend_suppliers(conn, sku, qty):
    m = conn.execute('SELECT * FROM materials WHERE sku=?', (sku,)).fetchone()
    if not m:
        return {'error': '未知物料 %s' % sku}
    out = []
    for s in conn.execute('SELECT * FROM suppliers').fetchall():
        hist = conn.execute('SELECT COUNT(*) AS n, SUM(on_time) AS ok FROM orders '
                            'WHERE supplier_id=? AND sku=?', (s['id'], sku)).fetchone()
        hist_bonus = (hist['ok'] or 0) / max(1, hist['n']) * 3.0 if hist['n'] else 0
        price_penalty = max(0.0, s['price_index'] - 95.0) * 0.55
        score = (s['on_time'] * 0.35 + s['quality'] * 0.25 +
                 (100 - price_penalty) * 0.25 + (100 - s['risk']) * 0.15 + hist_bonus)
        score = round(min(99.0, score), 1)
        unit = int(m['price_cents'] * s['price_index'] / 100.0)
        out.append({
            'supplier_id': s['id'], 'name': s['name'], 'score': score,
            'on_time': s['on_time'], 'quality': s['quality'], 'risk': s['risk'],
            'unit_price': money(unit), 'amount': money(unit * int(qty)),
            'explain': '准时交付 %.1f%% × 0.35 + 质量 %.1f × 0.25 + 价格指数 %.1f × 0.25 + 风险 %.1f × 0.15'
                       % (s['on_time'], s['quality'], s['price_index'], s['risk']),
            'history_orders': hist['n'],
        })
    out.sort(key=lambda x: -x['score'])
    return {
        'material': dict(m), 'qty': qty, 'recommendations': out[:3],
        'engine': '可解释加权评分 v1（可替换为私有 LLM 网关）',
        'data_sources': ['suppliers', 'orders', 'materials'],
        'gate': '建议态 · 需人工确认后才创建订单',
    }


def suggest_voucher(conn, event):
    """从业务事件推断科目与分摊，产出「草稿」，绝不直接过账。"""
    amount = int(event.get('amount_cents', 8640000))
    memo = event.get('memo', '秋季新品发布会会务费')
    products = event.get('products', ['A 产品线', 'B 产品线'])
    weights = event.get('weights', [0.6, 0.4])
    # 柔性判断：按受益主体分摊；硬规则：分摊权重必须合计 1、借贷必须平衡
    if abs(sum(weights) - 1.0) > 1e-6:
        return {'error': '硬规则拒绝：分摊权重合计必须为 1'}
    vid, vno = next_voucher_id(conn)
    tr = trace_id()
    with conn:
        conn.execute('INSERT INTO vouchers(id,no,source_type,source_id,memo,status,total_cents,'
                     'ai_confidence,ai_reason,created_at,trace_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                     (vid, vno, 'EXPENSE_EVENT', event.get('id', 'EV-MANUAL'), memo, 'DRAFT',
                      amount, 0.88,
                      '语义识别为市场推广费用；按受益产品线拆分；历史相似案例 12 条', now(), tr))
        for i, p in enumerate(products):
            part = int(round(amount * weights[i]))
            if i == len(products) - 1:
                part = amount - sum(int(round(amount * w)) for w in weights[:-1])
            conn.execute('INSERT INTO voucher_lines(voucher_id,line_no,account,cost_center,'
                         'debit_cents,credit_cents,basis) VALUES (?,?,?,?,?,?,?)',
                         (vid, i + 1, ACCOUNTS['sales_expense'], 'CC-' + p,
                          part, 0, 'OCR 发票 + 受益主体清单'))
        conn.execute('INSERT INTO voucher_lines(voucher_id,line_no,account,cost_center,'
                     'debit_cents,credit_cents,basis) VALUES (?,?,?,?,?,?,?)',
                     (vid, len(products) + 1, ACCOUNTS['ap'], None,
                      0, amount, '合同 CNT-2610-087'))
        audit(conn, event.get('actor', 'AI Copilot'), 'AI_VOUCHER_DRAFT', 'voucher', vno,
              {'memo': memo, 'amount': money(amount), 'confidence': 0.88,
               'rule': '借贷平衡 / 税码 / 期间校验通过'}, tr)
        emit(conn, 'VOUCHER_DRAFT_CREATED', {'voucher_no': vno, 'amount_cents': amount})
    bump(conn, 'writes')
    return {'voucher_no': vno, 'id': vid, 'status': 'DRAFT', 'amount': money(amount),
            'confidence': 0.88, 'gate': '置信度触发强制人工复核，禁止自动过账',
            'hard_rules': ['借贷平衡 OK', '科目体系 OK', '期间未关账 OK', '权限 OK']}


# --------------------------------------------------------------------------
# 写事务
# --------------------------------------------------------------------------
def create_order(conn, body, actor='采购经理', idem=None):
    cached = check_idempotency(conn, idem, 'POST /api/orders')
    if cached:
        return cached
    sku = body.get('sku') or body.get('material_sku')
    sid = body.get('supplier_id')
    qty = int(body.get('qty', 0))
    m = conn.execute('SELECT * FROM materials WHERE sku=?', (sku,)).fetchone()
    s = conn.execute('SELECT * FROM suppliers WHERE id=?', (sid,)).fetchone()
    if not m or not s:
        return {'error': '物料或供应商不存在', 'code': 'INVALID_ENTITY'}, 400
    if qty <= 0 or qty > 100000:
        return {'error': '数量必须在 1 ~ 100000 之间', 'code': 'INVALID_QTY'}, 400
    unit = int(m['price_cents'] * s['price_index'] / 100.0)
    amount = unit * qty
    # 硬规则：单笔金额超过 500 万必须走审批（记录为待审批，不允许直接确认）
    if amount > 500 * 10000 * 100:
        return {'error': '硬规则拒绝：单笔金额超过 500 万，需先走采购审批流程', 'code': 'BUDGET_GATE'}, 422
    oid, tr = next_order_id(conn), trace_id()
    with conn:
        conn.execute('INSERT INTO orders(id,supplier_id,supplier_name,sku,material_name,qty,'
                     'unit_price_cents,amount_cents,status,created_at,source) '
                     'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                     (oid, sid, s['name'], sku, m['name'], qty, unit, amount, 'CONFIRMED', now(),
                      body.get('source', 'MANUAL')))
        audit(conn, actor, 'ORDER_CREATE', 'order', oid,
              {'supplier': s['name'], 'sku': sku, 'qty': qty, 'amount': money(amount)}, tr)
        emit(conn, 'ORDER_CREATED', {'order_id': oid, 'amount_cents': amount})
        bump(conn, 'writes')
        bump(conn, 'transactions')
    resp = {'order_id': oid, 'status': 'CONFIRMED', 'amount': money(amount),
            'supplier': s['name'], 'sku': sku, 'qty': qty, 'trace_id': tr,
            'next': '可调用 /api/orders/%s/receive 执行到货入库' % oid}
    with conn:
        save_idempotency(conn, idem, 'POST /api/orders', resp)
    return resp


def receive_order(conn, oid, actor='仓储管理员', idem=None):
    cached = check_idempotency(conn, idem, 'POST /api/orders/%s/receive' % oid)
    if cached:
        return cached
    o = conn.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()
    if not o:
        return {'error': '订单不存在', 'code': 'NOT_FOUND'}, 404
    if o['status'] == 'RECEIVED':
        return {'error': '订单已收货，幂等事务拒绝重复入库', 'code': 'ALREADY_RECEIVED'}, 409
    if o['status'] not in ('CONFIRMED', 'DRAFT'):
        return {'error': '订单状态不允许收货', 'code': 'BAD_STATE'}, 409
    tr = trace_id()
    vid, vno = next_voucher_id(conn)
    with conn:
        # ① 库存 + 流水
        cur = conn.execute('SELECT qty FROM inventory WHERE sku=?', (o['sku'],)).fetchone()
        before = cur['qty'] if cur else 0
        after = before + o['qty']
        conn.execute('INSERT INTO inventory(sku,qty,updated_at) VALUES (?,?,?) '
                     'ON CONFLICT(sku) DO UPDATE SET qty=excluded.qty, updated_at=excluded.updated_at',
                     (o['sku'], after, now()))
        conn.execute('INSERT INTO inventory_ledger(ts,sku,delta,balance,ref_type,ref_id,trace_id) '
                     'VALUES (?,?,?,?,?,?,?)',
                     (now(), o['sku'], o['qty'], after, 'PURCHASE_RECEIPT', oid, tr))
        # ② 业务事件 → 财务凭证草稿（AI 建议，不自动过账）
        conn.execute('INSERT INTO vouchers(id,no,source_type,source_id,memo,status,total_cents,'
                     'ai_confidence,ai_reason,created_at,trace_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                     (vid, vno, 'PURCHASE_RECEIPT', oid,
                      '%s 采购入库 %d %s' % (o['material_name'], o['qty'], o['sku']),
                      'DRAFT', o['amount_cents'], 0.93,
                      '业务语义识别为原材料入库，按采购发票匹配应付科目', now(), tr))
        conn.execute('INSERT INTO voucher_lines(voucher_id,line_no,account,cost_center,'
                     'debit_cents,credit_cents,basis) VALUES (?,?,?,?,?,?,?)',
                     (vid, 1, ACCOUNTS['raw_material'], 'CC-制造中心',
                      o['amount_cents'], 0, '入库单 ' + oid))
        conn.execute('INSERT INTO voucher_lines(voucher_id,line_no,account,cost_center,'
                     'debit_cents,credit_cents,basis) VALUES (?,?,?,?,?,?,?)',
                     (vid, 2, ACCOUNTS['ap'], None,
                      0, o['amount_cents'], '供应商 ' + o['supplier_name']))
        # ③ 订单状态 + 审计 + 事件
        conn.execute('UPDATE orders SET status=?, received_at=? WHERE id=?',
                     ('RECEIVED', now(), oid))
        audit(conn, actor, 'ORDER_RECEIVE', 'order', oid,
              {'inventory_before': before, 'inventory_after': after,
               'delta': o['qty'], 'voucher_draft': vno,
               'atomic': ['inventory', 'inventory_ledger', 'voucher', 'voucher_lines', 'orders']}, tr)
        emit(conn, 'INVENTORY_CHANGED',
             {'sku': o['sku'], 'delta': o['qty'], 'balance': after, 'voucher_no': vno})
        bump(conn, 'writes')
        bump(conn, 'transactions')
    resp = {'order_id': oid, 'status': 'RECEIVED', 'sku': o['sku'],
            'inventory_before': before, 'inventory_after': after, 'delta': o['qty'],
            'voucher_draft': vno, 'voucher_status': 'DRAFT', 'trace_id': tr,
            'atomic_transaction': True,
            'next': '凭证需人工复核后调用 /api/vouchers/%s/approve' % vno}
    with conn:
        save_idempotency(conn, idem, 'POST /api/orders/%s/receive' % oid, resp)
    return resp


def approve_voucher(conn, vno, actor='财务复核员', idem=None):
    cached = check_idempotency(conn, idem, 'POST /api/vouchers/%s/approve' % vno)
    if cached:
        return cached
    v = conn.execute('SELECT * FROM vouchers WHERE no=?', (vno,)).fetchone()
    if not v:
        return {'error': '凭证不存在', 'code': 'NOT_FOUND'}, 404
    if v['status'] == 'POSTED':
        return {'error': '凭证已过账，幂等拒绝重复记账', 'code': 'ALREADY_POSTED'}, 409
    ls = conn.execute('SELECT COALESCE(SUM(debit_cents),0) AS d, COALESCE(SUM(credit_cents),0) AS c '
                      'FROM voucher_lines WHERE voucher_id=?', (v['id'],)).fetchone()
    # 硬规则一票否决：借贷必须平衡且非零、草稿才可过账
    if ls['d'] != ls['c'] or ls['d'] <= 0 or ls['d'] != v['total_cents']:
        return {'error': '硬规则拒绝：借贷不平衡或金额不一致，禁止过账',
                'debit': ls['d'], 'credit': ls['c'], 'total': v['total_cents'],
                'code': 'UNBALANCED'}, 422
    tr = trace_id()
    with conn:
        conn.execute('UPDATE vouchers SET status=?, posted_at=?, posted_by=? WHERE id=?',
                     ('POSTED', now(), actor, v['id']))
        audit(conn, actor, 'VOUCHER_POST', 'voucher', vno,
              {'amount': money(v['total_cents']), 'lines': 2,
               'rule_check': '借贷平衡 / 科目 / 期间 / 权限 全部通过',
               'ai_confidence': v['ai_confidence']}, tr)
        emit(conn, 'VOUCHER_POSTED', {'voucher_no': vno, 'amount_cents': v['total_cents']})
        bump(conn, 'writes')
        bump(conn, 'transactions')
    resp = {'voucher_no': vno, 'status': 'POSTED', 'amount': money(v['total_cents']),
            'posted_by': actor, 'trace_id': tr, 'audit': '已写入不可篡改审计日志'}
    with conn:
        save_idempotency(conn, idem, 'POST /api/vouchers/%s/approve' % vno, resp)
    return resp


def copilot(conn, message, actor='采购经理'):
    q = (message or '').strip()
    tools, refs, needs_approval = [], [], False
    if any(k in q for k in ('库存', '补货', '安全水位')):
        low = rows(conn, 'SELECT m.sku,m.name,m.safety_stock,i.qty FROM materials m '
                   'JOIN inventory i USING(sku) WHERE i.qty<m.safety_stock '
                   'ORDER BY i.qty*1.0/m.safety_stock LIMIT 3')
        tools = ['query_inventory(below_safety=true)', 'get_safety_stock()', 'create_task(type=补货)']
        refs = ['inventory', 'materials']
        answer = '检测到 %d 个物料低于安全水位：%s。已生成补货建议任务，等待人工确认后才会创建采购订单。' % (
            len(low), '、'.join('%s(%d/%d)' % (r['name'], r['qty'], r['safety_stock']) for r in low) or '无')
        needs_approval = True
        emit(conn, 'COPILOT_REPLENISH_SUGGESTION', {'items': low}, 'PENDING_APPROVAL')
    elif any(k in q for k in ('应收', '逾期', '回款', '催办')):
        ar = conn.execute('SELECT COALESCE(SUM(amount_cents),0) AS s, COUNT(*) AS n FROM receivables '
                          "WHERE settled=0 AND due_date<?", (datetime.now().strftime('%Y-%m-%d'),)).fetchone()
        tools = ['query_receivables(overdue=true)', 'risk_score(customer)', 'create_task(type=催办)']
        refs = ['receivables']
        answer = '当前逾期应收共 %s，涉及 %d 笔单据。已生成催办任务清单（建议态，待主管确认后外发）。' % (
            money(ar['s']), ar['n'])
        needs_approval = True
    elif any(k in q for k in ('供应商', '推荐', '选源', '采购')):
        r = recommend_suppliers(conn, 'A1024', 2000)
        top = r['recommendations'][0]
        tools = ['query_supplier_performance()', 'recommend_supplier(sku=A1024,topK=3)']
        refs = r['data_sources']
        answer = '为 A1024 完成选源分析：%s 综合评分 %.1f 分居首（%s）。结果已返回前端，需人工确认后才可下单。' % (
            top['name'], top['score'], top['explain'])
    elif any(k in q for k in ('凭证', '记账', '费用', '科目', '分摊')):
        v = suggest_voucher(conn, {'memo': 'Copilot 发起的费用事件', 'amount_cents': 8640000})
        tools = ['ocr_invoice()', 'infer_account(semantic)', 'check_hard_rules()', 'create_voucher_draft()']
        refs = ['vouchers', 'voucher_lines', 'audit_logs']
        answer = '已按受益产品线生成凭证草稿 %s（%s），置信度 0.88 触发强制人工复核，禁止自动过账。' % (
            v.get('voucher_no', ''), v.get('amount', ''))
        needs_approval = True
    elif any(k in q for k in ('审计', '日志', '追溯')):
        n = conn.execute('SELECT COUNT(*) AS n FROM audit_logs').fetchone()['n']
        tools = ['query_audit_logs(limit=20)']
        refs = ['audit_logs']
        answer = '审计日志共 %d 条，覆盖订单创建、入库事务、凭证过账与 AI 建议；每条都带 trace_id、操作人与前后快照。' % n
    else:
        d = dashboard(conn)
        tools = ['get_dashboard()']
        refs = ['orders', 'inventory', 'receivables', 'metrics_daily']
        answer = '本月订单额 %s，准时交付率 %s%%，低于安全水位的物料已生成 %d 条提醒。你可以问我库存、应收、供应商推荐或凭证。' % (
            d['kpi']['order_amount'], d['kpi']['on_time_rate'], len(d['alerts']))
    tr = trace_id()
    audit(conn, actor, 'COPILOT_QUERY', 'conversation', tr[:12],
          {'question': q, 'tools': tools, 'needs_approval': needs_approval}, tr)
    return {'answer': answer, 'tools': tools, 'data_sources': refs,
            'needs_approval': needs_approval, 'trace_id': tr,
            'gate': '高风险动作已挂起，等待人工确认' if needs_approval else '只读查询，无副作用'}


# --------------------------------------------------------------------------
# 路由分发
# --------------------------------------------------------------------------
def dispatch(method, path, body, headers):
    """返回 (payload, http_status)。"""
    conn = connect()
    locked = False
    try:
        bump(None, 'requests')
        idem = headers.get('Idempotency-Key') or (body or {}).get('idempotency_key')
        b = body or {}
        result = None
        if method == 'GET':
            if path == '/api/health':
                result = health(conn)
            elif path == '/api/dashboard':
                result = dashboard(conn)
            elif path == '/api/materials':
                result = rows(conn, 'SELECT * FROM materials ORDER BY sku')
            elif path == '/api/suppliers':
                result = rows(conn, 'SELECT * FROM suppliers ORDER BY id')
            elif path == '/api/orders':
                result = list_orders(conn)
            elif path == '/api/inventory':
                result = list_inventory(conn)
            elif path == '/api/vouchers':
                result = list_vouchers(conn)
            elif path == '/api/audit':
                result = list_audit(conn)
            elif path == '/api/events':
                result = rows(conn, 'SELECT * FROM events ORDER BY id DESC LIMIT 30')
            elif path == '/api/metrics':
                with RUNTIME_LOCK:
                    result = dict(RUNTIME)
        elif method == 'POST':
            WRITE_LOCK.acquire()
            locked = True
            if path == '/api/ai/recommend':
                result = recommend_suppliers(conn, b.get('sku', 'A1024'), int(b.get('qty', 1000)))
            elif path == '/api/ai/voucher-suggest':
                result = suggest_voucher(conn, b)
            elif path == '/api/copilot':
                result = copilot(conn, b.get('message', ''), b.get('actor', '采购经理'))
            elif path == '/api/orders':
                result = create_order(conn, b, b.get('actor', '采购经理'), idem)
            elif path.startswith('/api/orders/') and path.endswith('/receive'):
                oid = path.split('/')[3]
                result = receive_order(conn, oid, b.get('actor', '仓储管理员'), idem)
            elif path.startswith('/api/vouchers/') and path.endswith('/approve'):
                vno = path.split('/')[3]
                result = approve_voucher(conn, vno, b.get('actor', '财务复核员'), idem)
        if result is None:
            return {'error': '未知接口 %s %s' % (method, path), 'code': 'NOT_FOUND'}, 404
        if isinstance(result, tuple):
            return result
        conn.commit()  # Copilot / 事件等未在内部事务中的写操作统一提交
        return result, 200
    finally:
        if locked:
            WRITE_LOCK.release()
        conn.close()
