from __future__ import annotations

import json

from flask import Flask, jsonify, render_template_string, request

from backtest import run_backtest
from evaluator import evaluate_trades
from optimizer import optimize_parameters
from profile import load_strategy_profile
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
button{padding:10px 16px;margin:4px 8px 4px 0;border:0;border-radius:8px;cursor:pointer}
input{padding:9px;width:110px} table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border-bottom:1px solid #e5e7eb;padding:8px;text-align:left} th{background:#f9fafb}
#status{font-weight:600}.muted{color:#6b7280}pre{white-space:pre-wrap}
</style>
</head>
<body>
<div class="card">
<h2>A股短期波段量化选股器</h2>
<p class="muted">免费数据源 · 沪深A股扫描 · 内存自适应 · 市场环境识别 · 回测验证 · 受控参数优化。持股周期由信号动态决定，不把固定天数作为选股硬条件。</p>
<label>处理数量（空=全市场）： <input id="limit" type="number" min="1" placeholder="50"></label><br><br>
<button onclick="runScan()">开始扫描</button>
<button onclick="runBacktest()">运行回测</button>
<button onclick="runOptimize()">优化参数</button>
<button onclick="showProfile()">查看当前参数</button>
<p id="status">就绪</p>
</div>
<div class="card"><h3>结果</h3><div id="result">暂无结果</div></div>
<script>
const statusEl=()=>document.getElementById('status');
const resultEl=()=>document.getElementById('result');
function limitValue(){let v=document.getElementById('limit').value;return v?Number(v):null}
function table(rows){if(!rows||!rows.length)return '没有数据';let keys=Object.keys(rows[0]);return '<table><thead><tr>'+keys.map(k=>'<th>'+k+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+keys.map(k=>'<td>'+String(r[k]??'')+'</td>').join('')+'</tr>').join('')+'</tbody></table>'}
async function post(url,body){let r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});return await r.json()}
async function runScan(){statusEl().innerText='正在扫描...';resultEl().innerHTML='';let d=await post('/api/scan',{limit:limitValue()});statusEl().innerText=d.ok?'扫描完成':'扫描失败';resultEl().innerHTML=d.ok?table(d.rows):d.error}
async function runBacktest(){statusEl().innerText='正在回测...';resultEl().innerHTML='';let d=await post('/api/backtest',{limit:limitValue()});statusEl().innerText=d.ok?'回测完成':'回测失败';resultEl().innerHTML=d.ok?('<pre>'+JSON.stringify(d.metrics,null,2)+'</pre>'):d.error}
async function runOptimize(){let n=limitValue()||100;statusEl().innerText='正在训练/验证参数...';resultEl().innerHTML='';let d=await post('/api/optimize',{limit:n});statusEl().innerText=d.ok?'优化完成':'优化失败';resultEl().innerHTML=d.ok?(table(d.rows)+'<h4>活动参数</h4><pre>'+JSON.stringify(d.profile,null,2)+'</pre>'):d.error}
async function showProfile(){let r=await fetch('/api/profile');let d=await r.json();statusEl().innerText='当前参数';resultEl().innerHTML='<pre>'+JSON.stringify(d.profile,null,2)+'</pre>'}
</script>
</body>
</html>
"""


@app.get("/")
def index():
    return render_template_string(PAGE)


@app.get("/api/profile")
def api_profile():
    return jsonify({"ok": True, "profile": load_strategy_profile()})


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


@app.post("/api/optimize")
def api_optimize():
    try:
        payload = request.get_json(silent=True) or {}
        limit = int(payload.get("limit") or 100)
        result, best = optimize_parameters(limit=limit)
        rows = json.loads(result.to_json(orient="records", force_ascii=False))
        return jsonify({"ok": True, "rows": rows, "profile": best or load_strategy_profile()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
