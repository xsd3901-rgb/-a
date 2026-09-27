from __future__ import annotations

import json
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, render_template_string, request

from aquant.models.registry import model_status
from aquant.research.feature_selection import build_v2_candidate
from aquant.research.data_audit import run_data_audit
from aquant.research.data_repair import repair_failed_market_data
from aquant.research.feature_validation import run_feature_validation
from aquant.research.model_compare import run_model_comparison
from aquant.research.local_prepare import run_local_prepare
from aquant.research.readiness import release_readiness
from aquant.research.system_validation import run_system_validation
from aquant.research.stock_detail import stock_detail
from aquant.research.portfolio import simulate_portfolio
from aquant.research.walk_forward import run_walk_forward
from aquant.runtime.resources import current_memory_gb, current_profile
from aquant.version import __version__
from backtest import run_backtest
from bootstrap import bootstrap_market
from config import SETTINGS, ensure_directories
from evaluator import evaluate_by_market_regime, evaluate_trades
from eastmoney_formula import write_formula
from optimizer import optimize_parameters
from profile import load_strategy_profile
from scanner import scan_market


app = Flask(__name__)
JOBS: dict[str, dict] = {}
JOB_LOCK = threading.Lock()
ACTIVE_JOB_ID: str | None = None


def _records(frame: pd.DataFrame | None, limit: int = 100) -> list[dict]:
    if frame is None or frame.empty:
        return []
    return json.loads(
        frame.head(limit).to_json(
            orient="records",
            force_ascii=False,
            date_format="iso",
        )
    )


def _safe_dict(value: dict) -> dict:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _set_job(job_id: str, **values) -> None:
    with JOB_LOCK:
        job = JOBS.setdefault(job_id, {})
        job.update(values)
        job["updated_at"] = datetime.now().isoformat(timespec="seconds")


def _execute_task(job_id: str, action: str, payload: dict) -> None:
    global ACTIVE_JOB_ID
    try:
        _set_job(job_id, status="running", message="任务正在运行")
        limit_raw = payload.get("limit")
        limit = int(limit_raw) if limit_raw not in (None, "", 0, "0") else None
        refresh = bool(payload.get("refresh", False))
        horizon = int(payload.get("horizon") or 10)

        if action == "prepare_local":
            validation_limit = 200 if limit is None else max(1, min(int(limit), 500))
            steps, summary = run_local_prepare(
                bootstrap_limit=limit,
                validation_limit=validation_limit,
                horizon=horizon,
                refresh=refresh,
                progress=lambda message: _set_job(
                    job_id,
                    status="running",
                    message=message,
                ),
            )
            result = {
                "summary": summary,
                "rows": _records(steps, 20),
            }
        elif action == "readiness":
            frame, summary = release_readiness(audit_limit=limit)
            result = {
                "summary": summary,
                "rows": _records(frame, 50),
            }
        elif action == "repair_data":
            frame, summary = repair_failed_market_data(
                max_symbols=limit,
                progress=lambda message: _set_job(
                    job_id,
                    status="running",
                    message=message,
                ),
            )
            result = {
                "summary": summary,
                "rows": _records(frame, 100),
            }
        elif action == "audit_data":
            frame, summary = run_data_audit(limit=limit)
            result = {
                "summary": summary,
                "rows": _records(frame[frame["状态"] != "OK"] if not frame.empty else frame, 100),
            }
        elif action == "stock_detail":
            code = str(payload.get("code") or "").strip()
            if not code:
                raise ValueError("请输入股票代码")
            stock_summary, recent = stock_detail(
                code,
                refresh=refresh,
            )
            result = {
                "summary": stock_summary,
                "rows": _records(recent, 60),
            }
        elif action == "formula":
            path = write_formula()
            result = {
                "summary": {
                    "状态": "已导出",
                    "文件": path,
                    "说明": "东方财富公式是 V1 参考版；ST、停牌和历史交易制度仍以本地系统为准。",
                }
            }
        elif action == "bootstrap":
            status_frame, summary = bootstrap_market(
                limit=limit,
                refresh=refresh,
                progress=lambda message: _set_job(
                    job_id,
                    status="running",
                    message=message,
                ),
            )
            result = {
                "summary": summary,
                "rows": _records(status_frame.tail(100), 100),
            }
        elif action == "scan":
            frame = scan_market(
                limit=limit,
                refresh=refresh,
                progress=lambda message: _set_job(
                    job_id,
                    status="running",
                    message=message,
                ),
            )
            result = {
                "summary": {"入选数量": len(frame)},
                "rows": _records(frame, 100),
            }
        elif action == "backtest":
            trades = run_backtest(
                limit=limit,
                refresh=refresh,
                progress=lambda message: _set_job(
                    job_id,
                    status="running",
                    message=message,
                ),
            )
            metrics = evaluate_trades(trades)
            by_regime = evaluate_by_market_regime(trades)
            _, equity, portfolio = simulate_portfolio(trades)
            result = {
                "metrics": metrics,
                "portfolio": portfolio,
                "by_regime": _records(by_regime, 20),
                "trades": _records(trades, 80),
                "equity_tail": _records(equity.tail(30), 30),
            }
        elif action == "features":
            feature_limit = 200 if limit is None else limit
            frame = run_feature_validation(
                limit=feature_limit,
                refresh=refresh,
                progress=lambda message: _set_job(
                    job_id,
                    status="running",
                    message=message,
                ),
            )
            result = {
                "summary": {"结果行数": len(frame)},
                "rows": _records(frame, 80),
            }
        elif action == "select_features":
            selected, candidate = build_v2_candidate()
            result = {
                "candidate": candidate,
                "rows": _records(selected, 50),
            }
        elif action == "walk_forward":
            folds, summary = run_walk_forward(horizon=horizon)
            result = {
                "summary": summary,
                "rows": _records(folds, 80),
            }
        elif action == "compare_models":
            comparison, summary = run_model_comparison(horizon=horizon)
            result = {
                "summary": summary,
                "rows": _records(comparison, 10),
            }
        elif action == "validate_system":
            validate_limit = int(limit or 200)
            comparison, summary = run_system_validation(
                feature_limit=validate_limit,
                backtest_limit=validate_limit,
                horizon=horizon,
                refresh=refresh,
            )
            result = {
                "summary": summary,
                "rows": _records(comparison, 10),
            }
        elif action == "optimize":
            opt_limit = int(limit or 100)
            frame, best = optimize_parameters(
                limit=opt_limit,
                refresh=refresh,
            )
            result = {
                "profile": best or load_strategy_profile(),
                "rows": _records(frame, 50),
            }
        else:
            raise ValueError(f"未知任务: {action}")

        _set_job(
            job_id,
            status="completed",
            message="任务完成",
            result=_safe_dict(result),
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )
    except Exception as exc:
        _set_job(
            job_id,
            status="failed",
            message=str(exc),
            error=str(exc),
            traceback=traceback.format_exc(limit=12),
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )
    finally:
        with JOB_LOCK:
            if ACTIVE_JOB_ID == job_id:
                ACTIVE_JOB_ID = None


def _start_task(action: str, payload: dict) -> tuple[dict, int]:
    global ACTIVE_JOB_ID
    with JOB_LOCK:
        if ACTIVE_JOB_ID:
            active = JOBS.get(ACTIVE_JOB_ID, {})
            if active.get("status") in {"queued", "running"}:
                return {
                    "ok": False,
                    "error": "已有任务正在运行，请等待完成后再启动新任务。",
                    "job_id": ACTIVE_JOB_ID,
                }, 409

        job_id = uuid.uuid4().hex[:12]
        ACTIVE_JOB_ID = job_id
        JOBS[job_id] = {
            "id": job_id,
            "action": action,
            "status": "queued",
            "message": "任务已进入队列",
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }

    thread = threading.Thread(
        target=_execute_task,
        args=(job_id, action, payload),
        daemon=True,
        name=f"aquant-{action}-{job_id}",
    )
    thread.start()
    return {"ok": True, "job_id": job_id}, 202


def _report_inventory() -> list[dict]:
    ensure_directories()
    files: list[dict] = []
    for path in sorted(SETTINGS.report_dir.glob("*")):
        if not path.is_file():
            continue
        stat = path.stat()
        files.append(
            {
                "文件": path.name,
                "大小KB": round(stat.st_size / 1024.0, 1),
                "更新时间": datetime.fromtimestamp(stat.st_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }
        )
    return files[-30:]


PAGE = r"""
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>A-Quant 沪深A股量化终端</title>
<style>
:root{--bg:#0b1020;--panel:#121a2f;--panel2:#17213b;--text:#e9eefc;--muted:#95a2c6;--line:#273454;--accent:#7aa2ff;--ok:#57d39b;--warn:#ffcc66;--bad:#ff7b8a}
*{box-sizing:border-box}
body{margin:0;background:linear-gradient(135deg,#080d1b,#0d1430 55%,#111a33);color:var(--text);font-family:Inter,"Microsoft YaHei",Arial,sans-serif}
.wrap{max-width:1500px;margin:0 auto;padding:22px}
.header{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:18px}
h1{margin:0;font-size:28px}.sub{color:var(--muted);margin-top:8px;line-height:1.6}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:14px}
.card{background:rgba(18,26,47,.96);border:1px solid var(--line);border-radius:14px;padding:16px;box-shadow:0 10px 30px rgba(0,0,0,.18)}
.k{color:var(--muted);font-size:12px}.v{font-size:20px;margin-top:6px;font-weight:700}
.controls{display:flex;flex-wrap:wrap;gap:9px;align-items:center}
input,select{background:#0d152a;color:var(--text);border:1px solid var(--line);border-radius:9px;padding:9px 11px}
button{background:#24355c;color:var(--text);border:1px solid #344a79;border-radius:9px;padding:10px 14px;cursor:pointer;font-weight:600}
button:hover{background:#2d4476}button.primary{background:#315ecb;border-color:#4776df}button.danger{background:#63313b}
.status{padding:12px 14px;border-radius:10px;background:#0d152a;border:1px solid var(--line);margin-top:12px;white-space:pre-wrap}
.tabs{display:flex;gap:8px;margin:14px 0}.pill{font-size:12px;color:var(--muted);border:1px solid var(--line);padding:6px 9px;border-radius:999px}
table{border-collapse:collapse;width:100%;font-size:12px;min-width:900px}th,td{border-bottom:1px solid var(--line);padding:8px;text-align:left;white-space:nowrap}th{position:sticky;top:0;background:#18233d}
.tablewrap{overflow:auto;max-height:520px;border:1px solid var(--line);border-radius:10px}
pre{white-space:pre-wrap;word-break:break-word;background:#0b1327;padding:12px;border-radius:10px;color:#cdd8f7;max-height:500px;overflow:auto}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.section-title{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
@media(max-width:900px){.grid{grid-template-columns:1fr 1fr}.header{display:block}}
@media(max-width:600px){.grid{grid-template-columns:1fr}.wrap{padding:12px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <div>
      <h1>A-Quant 沪深A股量化终端</h1>
      <div class="sub">免费数据源 · 历史股票池 · 点时ST/停牌 · 未复权真实成交层 · 特征验证 · Walk-Forward · V1/V2 对照 · 有限资金组合 · 逐日盯市回撤</div>
    </div>
    <span class="pill">v{{ version }} · Local Research Terminal</span>
  </div>

  <div class="grid">
    <div class="card"><div class="k">运行资源档</div><div class="v" id="runtime">--</div></div>
    <div class="card"><div class="k">可用内存</div><div class="v" id="memory">--</div></div>
    <div class="card"><div class="k">当前策略阈值</div><div class="v" id="threshold">--</div></div>
    <div class="card"><div class="k">模型状态</div><div class="v" id="modelState">--</div></div>
  </div>

  <div class="card">
    <div class="section-title"><b>任务控制</b><span class="pill">同一时间只运行一个重任务</span></div>
    <div class="controls">
      <label>股票数量 <input id="limit" type="number" min="0" placeholder="空=默认/全市场"></label>
      <label>预测窗
        <select id="horizon"><option>5</option><option selected>10</option><option>20</option></select>
      </label>
      <label>单股代码 <input id="stockCode" type="text" maxlength="6" placeholder="例如600000"></label>
      <label><input id="refresh" type="checkbox"> 强制刷新</label>
    </div>
    <div class="controls" style="margin-top:12px">
      <button class="primary" onclick="runTask('prepare_local')">首次完整准备</button>
      <button class="primary" onclick="runTask('bootstrap')">建立/更新本地数据库</button>
      <button onclick="runTask('readiness')">就绪检查</button>
      <button onclick="runTask('audit_data')">本地数据审计</button>
      <button onclick="runTask('repair_data')">修复失败数据</button>
      <button class="primary" onclick="runTask('scan')">全市场扫描</button>
      <button onclick="runTask('stock_detail')">查看单股详情</button>
      <button onclick="runTask('backtest')">V1真实成交回测</button>
      <button onclick="runTask('features')">特征有效性</button>
      <button onclick="runTask('select_features')">生成V2候选</button>
      <button onclick="runTask('walk_forward')">Walk-Forward</button>
      <button onclick="runTask('compare_models')">V1 / V2 对比</button>
      <button onclick="runTask('validate_system')">一键系统验收</button>
      <button onclick="runTask('optimize')">参数优化</button>
      <button onclick="runTask('formula')">导出东财公式</button>
      <button onclick="refreshStatus()">刷新状态</button>
    </div>
    <div id="status" class="status">就绪。</div>
  </div>

  <div class="card">
    <div class="section-title"><b>任务结果</b><span id="jobBadge" class="pill">无任务</span></div>
    <div id="summary"></div>
    <div id="result">暂无结果。</div>
  </div>

  <div class="card">
    <div class="section-title"><b>本地报告</b><span class="pill" id="reportPath"></span></div>
    <div class="tablewrap"><div id="reports">加载中...</div></div>
  </div>
</div>

<script>
let activeJob=null, pollTimer=null;
const $=id=>document.getElementById(id);
function esc(v){return String(v??'').replace(/[&<>"']/g,s=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]))}
function table(rows){
  if(!rows||!rows.length)return '<div class="sub">没有表格数据</div>';
  const keys=Object.keys(rows[0]);
  return '<div class="tablewrap"><table><thead><tr>'+keys.map(k=>'<th>'+esc(k)+'</th>').join('')+
  '</tr></thead><tbody>'+rows.map(r=>'<tr>'+keys.map(k=>'<td>'+esc(r[k])+'</td>').join('')+
  '</tr>').join('')+'</tbody></table></div>';
}
function pretty(o){return '<pre>'+esc(JSON.stringify(o,null,2))+'</pre>'}
function payload(){
  let raw=$('limit').value.trim();
  return {limit:raw===''?null:Number(raw),refresh:$('refresh').checked,horizon:Number($('horizon').value),code:$('stockCode').value.trim()};
}
async function runTask(action){
  if(action==='prepare_local'){
    const ok=confirm('首次完整准备会建立/更新沪深全市场本地数据库，并继续运行研究验收。首次执行可能耗时较长。确认开始吗？');
    if(!ok)return;
  }
  $('status').textContent='正在提交任务...';
  $('result').innerHTML='';
  $('summary').innerHTML='';
  const r=await fetch('/api/run/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload())});
  const d=await r.json();
  if(!d.ok){$('status').textContent=d.error||'启动失败';if(d.job_id){activeJob=d.job_id;startPolling()}return}
  activeJob=d.job_id;$('jobBadge').textContent=action+' · '+activeJob;$('status').textContent='任务已启动，正在后台运行...';startPolling();
}
function startPolling(){
  if(pollTimer)clearInterval(pollTimer);
  pollJob();pollTimer=setInterval(pollJob,1500);
}
async function pollJob(){
  if(!activeJob)return;
  const r=await fetch('/api/job/'+activeJob);const d=await r.json();
  if(!d.ok)return;
  const j=d.job;
  $('status').textContent=(j.status||'')+' · '+(j.message||'');
  if(j.status==='completed'){
    clearInterval(pollTimer);pollTimer=null;
    $('status').innerHTML='<span class="ok">任务完成</span> · '+esc(j.finished_at||'');
    renderResult(j.result||{});refreshStatus();
  }else if(j.status==='failed'){
    clearInterval(pollTimer);pollTimer=null;
    $('status').innerHTML='<span class="bad">任务失败：</span>'+esc(j.error||j.message||'');
    $('result').innerHTML=pretty({error:j.error,traceback:j.traceback});
  }
}
function renderResult(r){
  let blocks=[];
  if(r.summary)blocks.push('<h3>汇总</h3>'+pretty(r.summary));
  if(r.metrics)blocks.push('<h3>交易统计</h3>'+pretty(r.metrics));
  if(r.portfolio)blocks.push('<h3>有限资金组合</h3>'+pretty(r.portfolio));
  if(r.profile)blocks.push('<h3>策略参数</h3>'+pretty(r.profile));
  if(r.candidate)blocks.push('<h3>V2候选模型</h3>'+pretty(r.candidate));
  if(r.rows)blocks.push('<h3>结果</h3>'+table(r.rows));
  if(r.by_regime&&r.by_regime.length)blocks.push('<h3>市场环境拆分</h3>'+table(r.by_regime));
  if(r.trades&&r.trades.length)blocks.push('<h3>交易样本</h3>'+table(r.trades));
  $('result').innerHTML=blocks.join('')||pretty(r);
}
async function refreshStatus(){
  const r=await fetch('/api/status');const d=await r.json();
  if(!d.ok)return;
  $('runtime').textContent=d.runtime.name;
  $('memory').textContent=d.memory.available_gb+' GB';
  $('threshold').textContent=d.profile.score_threshold;
  $('modelState').textContent=d.models.active_model+' / '+d.models.v2_state;
  $('reportPath').textContent=d.paths.reports;
  $('reports').innerHTML=table(d.reports);
  if(d.active_job && !activeJob){
    activeJob=d.active_job;
    $('jobBadge').textContent='运行中 · '+activeJob;
    startPolling();
  }
}
refreshStatus();
</script>
</body>
</html>
"""


@app.get("/")
def index():
    return render_template_string(PAGE, version=__version__)


@app.get("/api/status")
def api_status():
    ensure_directories()
    total, available = current_memory_gb()
    runtime = current_profile()
    return jsonify(
        {
            "ok": True,
            "version": __version__,
            "runtime": _safe_dict(runtime.__dict__),
            "memory": {
                "total_gb": round(total, 2),
                "available_gb": round(available, 2),
            },
            "profile": load_strategy_profile(),
            "models": model_status(),
            "paths": {
                "project": str(SETTINGS.project_root),
                "data_store": str(SETTINGS.data_store_dir),
                "reports": str(SETTINGS.report_dir),
            },
            "reports": _report_inventory(),
            "active_job": ACTIVE_JOB_ID,
        }
    )


@app.post("/api/run/<action>")
def api_run(action: str):
    allowed = {
        "repair_data",
        "prepare_local",
        "readiness",
        "audit_data",
        "stock_detail",
        "formula",
        "bootstrap",
        "scan",
        "backtest",
        "features",
        "select_features",
        "walk_forward",
        "compare_models",
        "validate_system",
        "optimize",
    }
    if action not in allowed:
        return jsonify({"ok": False, "error": "未知任务"}), 404
    payload = request.get_json(silent=True) or {}
    body, status = _start_task(action, payload)
    return jsonify(body), status


@app.get("/api/job/<job_id>")
def api_job(job_id: str):
    with JOB_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return jsonify({"ok": False, "error": "任务不存在"}), 404
        return jsonify({"ok": True, "job": _safe_dict(job)})


# 兼容旧接口。
@app.get("/api/profile")
def api_profile():
    return jsonify({"ok": True, "profile": load_strategy_profile()})


if __name__ == "__main__":
    ensure_directories()
    print("=" * 68)
    print("A-Quant 沪深A股量化终端")
    print(f"项目目录: {SETTINGS.project_root}")
    print(f"本地数据: {SETTINGS.data_store_dir}")
    print(f"报告目录: {SETTINGS.report_dir}")
    print("浏览器地址: http://127.0.0.1:5000")
    print("=" * 68)
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
