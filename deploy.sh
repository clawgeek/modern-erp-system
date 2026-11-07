#!/usr/bin/env bash
# 云枢 ERP 一键部署脚本（本地 / Docker 两种模式）
#   ./deploy.sh local     零依赖本地启动
#   ./deploy.sh docker    Docker Compose 启动（含健康检查与数据卷）
#   ./deploy.sh check     对已运行服务做健康检查与业务冒烟
set -euo pipefail

export PORT="${PORT:-8766}"
BASE="http://127.0.0.1:${PORT}"
REPO="https://github.com/clawgeek/modern-erp-system"

banner() { echo "================================================================"; echo "  云枢 ERP · $1"; echo "  $REPO"; echo "================================================================"; }

check() {
  banner "健康检查"
  python - <<'PY'
import json, urllib.request, os
base = "http://127.0.0.1:" + os.environ.get("PORT", "8766")
for path in ("/api/health", "/api/dashboard", "/api/orders", "/api/vouchers"):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        d = json.load(r)
    if path == "/api/health":
        print("health   :", d["status"], d["database"], "tables =", d["tables"])
    elif path == "/api/dashboard":
        print("dashboard:", d["kpi"]["order_amount"], "订单 / 准时交付", str(d["kpi"]["on_time_rate"]) + "%")
    else:
        print(path.ljust(10), ":", len(d), "条记录")
print("ALL CHECKS PASSED")
PY
}

case "${1:-local}" in
  local)
    banner "本地零依赖部署"
    python3 -V || python -V
    python server.py --port "$PORT" &
    SRV=$!
    trap 'kill $SRV 2>/dev/null || true' EXIT
    for i in $(seq 1 30); do
      if curl -fsS "$BASE/api/health" >/dev/null 2>&1; then break; fi
      sleep 0.4
    done
    check
    echo "服务运行中：$BASE  （Ctrl+C 停止）"
    wait $SRV
    ;;
  docker)
    banner "Docker Compose 部署"
    docker compose up -d --build
    for i in $(seq 1 40); do
      if curl -fsS "$BASE/api/health" >/dev/null 2>&1; then break; fi
      sleep 0.5
    done
    docker compose ps
    check
    ;;
  check)
    check
    ;;
  *)
    echo "用法: ./deploy.sh [local|docker|check]"; exit 1 ;;
esac
