from __future__ import annotations

import json

from flask import Flask, jsonify, render_template_string, request

from backtest import run_backtest
from evaluator import evaluate_trades
from scanner import scan_market

app = Flask(__name__)

PAGE = r"""
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>A股短期波段量化选股器</title>
<style>
body{font-family:Arial,"Microsoft YaHei",sans-serif;margin:24px;background:#f5f7fb;color:#1f2937}
.card{background:white;padding:18px;border-radius:12px;margin-bottom:16px;box-shadow:0 2px 12px rgba(0,0,0,.06)}
button{padding:10px 16px;margin-right:8px;border:0;border-radius:8px;cursor:pointer}
input{padding:9px;width:100px} table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border-bottom:1px solid #e5e7eb;padding:8px;text-align:left} th{background:#f9fafb}
#status{font-weight:600} .muted{color:#6b7280}
</style>
</head>
<body>
<div class="card">
<h2>A股短期波段量化选股器</h2>
<p class="muted">免费数据源 · 全市场扫描 · 低内存 · 回测验证。持股周期由信号动态决定，不把固定天数作为选股硬条件。</p>
<label>测试数量（空=全市场）： <input id="limit" type="number" min="1" placeholder="50"></label>
<button onclick="runScan()">开始扫描</button>
<button onclick="runBacktest()">运行回测</button>
<p id="status">就绪</p>
</div>
<div class="card"><h3>结果</h3><div id="result">暂无结果</div></div>
<script>
function limitValue(){let v=document.getElementById('limit').value;return v?Number(v):null}
function table(rows){if(!rows || !rows.length)return '没有数据';let keys=Object.keys(rows[0]);return '<table><thead><tr>'+keys.map(k=>'<th>'+k+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+keys.map(k=>'<td>'+String(r[k]??'')+'</td>').join('')+'</tr>').join('')+'</tbody></table>'}
async function runScan(){status.innerText='正在扫描...';result.innerHTML='';let r=await fetch('/api/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({limit:limitValue()})});let d=await r.json();status.innerText=d.ok?'扫描完成':'扫描失败';result.innerHTML=d.ok?table(d.rows):d.error}
async function runBacktest(){status.innerText='正在回测...';result.innerHTML='';let r=await fetch('/api/backtest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({limit:limitValue()})});let d=await r.json();status.innerText=d.ok?'回测完成':'回测失败';result.innerHTML=d.ok?('<pre>'+JSON.stringify(d.metrics,null,2)+'</pre>'):d.error}
</script>
</body>
</html>
"""


@app.get("/")
def index():
    return render_template_string(PAGE)


@app.post("/api/scan")
def api_scan():
    try:
        payload = request.get_json(silent=True) or {}
        limit = payload.get("limit")
        df = scan_market(limit=int(limit) if limit else None)
        rows = json.loads(df.to_json(orient="records", force_ascii=False))
        return jsonify({"ok": True, "rows": rows})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/backtest")
def api_backtest():
    try:
        payload = request.get_json(silent=True) or {}
        limit = payload.get("limit")
        trades = run_backtest(limit=int(limit) if limit else None)
        return jsonify({"ok": True, "metrics": evaluate_trades(trades)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
