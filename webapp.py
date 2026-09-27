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
from aquant.research.source_quality import run_source_quality
from aquant.research.strategy_ablation import run_v1_ablation
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
        elif action == "source_quality":
            providers, usage, crosscheck, summary = run_source_quality(
                crosscheck_limit=limit,
                include_crosscheck=True,
            )
            problems = (
                crosscheck[
                    crosscheck["状态"].astype(str) != "一致"
                ]
                if not crosscheck.empty
                else crosscheck
            )
            result = {
                "summary": summary,
                "rows": _records(providers, 50),
                "usage": _records(usage, 50),
                "crosscheck": _records(problems, 100),
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
        elif action == "ablation":
            detail, summary_table, summary = run_v1_ablation()
            result = {
                "summary": summary,
                "rows": _records(summary_table, 50),
                "ablation_detail": _records(detail, 100),
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
:root{
  --bg:#08101f;--bg2:#0d1730;--panel:#111c33;--panel2:#16233e;
  --text:#edf3ff;--muted:#8fa1c4;--line:#263759;--line2:#33496f;
  --accent:#6d9cff;--accent2:#5ce1d0;--ok:#55d59a;--warn:#f5c96a;
  --bad:#ff7585;--shadow:0 18px 44px rgba(0,0,0,.24)
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{
  margin:0;color:var(--text);font-family:Inter,"Microsoft YaHei",Arial,sans-serif;
  background:
    radial-gradient(circle at 12% 0%,rgba(75,113,210,.18),transparent 28%),
    radial-gradient(circle at 95% 12%,rgba(61,197,188,.10),transparent 24%),
    linear-gradient(145deg,var(--bg),var(--bg2) 58%,#10182b);
  min-height:100vh
}
button,input,select{font:inherit}
.shell{max-width:1580px;margin:0 auto;padding:22px}
.hero{
  display:flex;justify-content:space-between;gap:18px;align-items:center;
  padding:20px 22px;margin-bottom:15px;border:1px solid var(--line);
  border-radius:20px;background:linear-gradient(125deg,rgba(20,33,62,.98),rgba(13,24,48,.92));
  box-shadow:var(--shadow)
}
.brand{display:flex;align-items:center;gap:14px}
.logo{
  width:48px;height:48px;border-radius:15px;display:grid;place-items:center;
  font-weight:900;font-size:20px;letter-spacing:-1px;color:#061322;
  background:linear-gradient(135deg,var(--accent2),#8eb0ff)
}
h1{margin:0;font-size:28px;letter-spacing:-.4px}
.sub{color:var(--muted);margin-top:7px;line-height:1.6;font-size:13px}
.hero-right{display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-end}
.pill{
  font-size:12px;color:#b8c8e9;border:1px solid var(--line2);
  padding:7px 10px;border-radius:999px;background:rgba(11,21,42,.7)
}
.pill.live{color:var(--ok);border-color:rgba(85,213,154,.35)}
.metrics{
  display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:11px;margin-bottom:15px
}
.metric{
  min-height:96px;padding:15px 16px;border:1px solid var(--line);border-radius:16px;
  background:rgba(17,28,51,.94);box-shadow:0 10px 30px rgba(0,0,0,.13)
}
.metric .k{color:var(--muted);font-size:12px}
.metric .v{font-size:19px;font-weight:800;margin-top:9px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.metric .hint{color:#7185aa;font-size:11px;margin-top:5px}
.workspace{display:grid;grid-template-columns:390px minmax(0,1fr);gap:14px;align-items:start}
.card{
  background:rgba(17,28,51,.96);border:1px solid var(--line);border-radius:17px;
  padding:16px;box-shadow:0 12px 34px rgba(0,0,0,.16)
}
.sticky{position:sticky;top:14px}
.section-title{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:12px}
.section-title b{font-size:15px}
.control-grid{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-bottom:13px}
.field{display:flex;flex-direction:column;gap:5px;color:var(--muted);font-size:12px}
.field.full{grid-column:1/-1}
input,select{
  width:100%;background:#0c162b;color:var(--text);border:1px solid var(--line);
  border-radius:10px;padding:10px 11px;outline:none;transition:.15s
}
input:focus,select:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(109,156,255,.12)}
.toggle{
  display:flex;align-items:center;gap:8px;padding:10px 11px;background:#0c162b;
  border:1px solid var(--line);border-radius:10px;color:var(--text)
}
.toggle input{width:auto}
.group{border-top:1px solid var(--line);padding-top:13px;margin-top:13px}
.group:first-of-type{border-top:0;padding-top:0;margin-top:0}
.group-title{font-size:12px;color:#a9b9d8;font-weight:800;margin-bottom:8px;text-transform:uppercase;letter-spacing:.55px}
.actions{display:grid;grid-template-columns:1fr 1fr;gap:8px}
button{
  border:1px solid #34496f;background:#1c2c4b;color:var(--text);
  border-radius:10px;padding:10px 11px;cursor:pointer;font-weight:700;
  transition:transform .12s ease,background .12s ease,border-color .12s ease;
  text-align:left
}
button:hover{background:#253a62;border-color:#496796;transform:translateY(-1px)}
button.primary{background:linear-gradient(135deg,#315fca,#4077dc);border-color:#5684e5}
button.teal{background:linear-gradient(135deg,#14796e,#1d9284);border-color:#2eaa9b}
button.ghost{background:#111c33}
button:disabled{opacity:.45;cursor:not-allowed;transform:none}
.status-card{margin-bottom:14px}
.status-line{display:flex;gap:11px;align-items:flex-start}
.status-dot{
  width:10px;height:10px;border-radius:50%;margin-top:5px;background:var(--ok);
  box-shadow:0 0 0 5px rgba(85,213,154,.10)
}
.status-dot.running{background:var(--accent);box-shadow:0 0 0 5px rgba(109,156,255,.12)}
.status-dot.failed{background:var(--bad);box-shadow:0 0 0 5px rgba(255,117,133,.10)}
.status-main{flex:1;min-width:0}
.status-text{white-space:pre-wrap;word-break:break-word;line-height:1.55}
.progress-shell{
  margin-top:12px;height:8px;background:#0a1325;border:1px solid var(--line);
  border-radius:999px;overflow:hidden
}
.progress-bar{
  height:100%;width:0;background:linear-gradient(90deg,var(--accent),var(--accent2));
  transition:width .3s ease
}
.result-head{display:flex;justify-content:space-between;gap:10px;align-items:center;margin-bottom:11px}
.summary-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px;margin:10px 0 14px}
.summary-item{
  border:1px solid var(--line);border-radius:12px;padding:11px 12px;background:#0c162b;
  min-height:70px
}
.summary-item .sk{font-size:11px;color:var(--muted);margin-bottom:6px}
.summary-item .sv{font-size:15px;font-weight:800;word-break:break-word}
.block-title{font-size:14px;margin:18px 0 8px}
.tablewrap{
  overflow:auto;max-height:540px;border:1px solid var(--line);border-radius:12px;background:#0c162b
}
table{border-collapse:collapse;width:100%;font-size:12px;min-width:850px}
th,td{border-bottom:1px solid #1e2d49;padding:9px 10px;text-align:left;white-space:nowrap}
th{
  position:sticky;top:0;z-index:1;background:#17243f;color:#bed0ef;font-weight:800
}
tbody tr:hover{background:#14223d}
pre{
  white-space:pre-wrap;word-break:break-word;background:#0b1428;padding:12px;
  border:1px solid var(--line);border-radius:11px;color:#cbd8f0;max-height:480px;overflow:auto
}
.empty{
  min-height:210px;display:grid;place-items:center;text-align:center;color:var(--muted);
  border:1px dashed var(--line);border-radius:13px;background:rgba(9,18,36,.35)
}
.empty strong{display:block;color:#c7d5ef;font-size:15px;margin-bottom:6px}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.chart{
  width:100%;height:160px;border:1px solid var(--line);border-radius:12px;
  background:#0b1428;margin:8px 0 14px;padding:8px
}
.reports-card{margin-top:14px}
.report-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.mini-note{font-size:11px;color:var(--muted);line-height:1.5}
@media(max-width:1150px){
  .metrics{grid-template-columns:repeat(3,minmax(0,1fr))}
  .workspace{grid-template-columns:1fr}.sticky{position:static}
}
@media(max-width:760px){
  .shell{padding:11px}.hero{align-items:flex-start}.hero-right{display:none}
  .brand{align-items:flex-start}.logo{width:42px;height:42px}
  h1{font-size:22px}.metrics{grid-template-columns:1fr 1fr}
  .control-grid,.actions,.summary-grid{grid-template-columns:1fr 1fr}
}
@media(max-width:500px){
  .metrics,.summary-grid,.control-grid,.actions{grid-template-columns:1fr}
}
</style>
</head>
<body>
<div class="shell">
  <header class="hero">
    <div class="brand">
      <div class="logo">AQ</div>
      <div>
        <h1>A-Quant 沪深A股量化终端</h1>
        <div class="sub">扫描 · 数据质量 · 真实成交回测 · Walk-Forward · V1/V2 研究验收</div>
      </div>
    </div>
    <div class="hero-right">
      <span class="pill live">● 本地终端</span>
      <span class="pill">v{{ version }}</span>
      <span class="pill">Free Data Stack</span>
    </div>
  </header>

  <section class="metrics">
    <div class="metric"><div class="k">运行资源档</div><div class="v" id="runtime">--</div><div class="hint">自动按内存与CPU调整</div></div>
    <div class="metric"><div class="k">可用内存</div><div class="v" id="memory">--</div><div class="hint">长任务会自适应降载</div></div>
    <div class="metric"><div class="k">当前策略阈值</div><div class="v" id="threshold">--</div><div class="hint">活动 V1/V2 配置</div></div>
    <div class="metric"><div class="k">模型状态</div><div class="v" id="modelState">--</div><div class="hint">候选模型不会自动晋级</div></div>
    <div class="metric"><div class="k">任务状态</div><div class="v" id="taskState">空闲</div><div class="hint" id="taskHint">可启动新任务</div></div>
  </section>

  <div class="workspace">
    <aside class="card sticky">
      <div class="section-title">
        <b>控制台</b>
        <span class="pill">一次一个重任务</span>
      </div>

      <div class="control-grid">
        <label class="field">股票数量
          <input id="limit" type="number" min="0" placeholder="空 = 默认 / 全市场">
        </label>
        <label class="field">预测窗口
          <select id="horizon">
            <option>5</option><option selected>10</option><option>20</option>
          </select>
        </label>
        <label class="field">单股代码
          <input id="stockCode" type="text" maxlength="6" inputmode="numeric" placeholder="例如 600000">
        </label>
        <label class="field">
          数据刷新
          <span class="toggle"><input id="refresh" type="checkbox"> 强制刷新 / 放弃旧断点</span>
        </label>
      </div>

      <div class="group">
        <div class="group-title">日常使用</div>
        <div class="actions">
          <button class="primary task-btn" onclick="runTask('scan')">全市场扫描</button>
          <button class="task-btn" onclick="runTask('stock_detail')">单股详情</button>
          <button class="task-btn" onclick="runTask('backtest')">V1真实回测</button>
          <button class="ghost" onclick="refreshStatus()">刷新状态</button>
        </div>
      </div>

      <div class="group">
        <div class="group-title">数据中心</div>
        <div class="actions">
          <button class="teal task-btn" onclick="runTask('bootstrap')">更新本地数据库</button>
          <button class="task-btn" onclick="runTask('audit_data')">数据完整性审计</button>
          <button class="task-btn" onclick="runTask('source_quality')">数据源质量追踪</button>
          <button class="task-btn" onclick="runTask('repair_data')">定向修复失败</button>
        </div>
      </div>

      <div class="group">
        <div class="group-title">策略研究</div>
        <div class="actions">
          <button class="task-btn" onclick="runTask('features')">特征有效性</button>
          <button class="task-btn" onclick="runTask('ablation')">V1规则消融</button>
          <button class="task-btn" onclick="runTask('select_features')">生成V2候选</button>
          <button class="task-btn" onclick="runTask('walk_forward')">Walk-Forward</button>
          <button class="task-btn" onclick="runTask('compare_models')">V1 / V2 对比</button>
          <button class="task-btn" onclick="runTask('validate_system')">一键系统验收</button>
          <button class="task-btn" onclick="runTask('optimize')">参数优化</button>
        </div>
      </div>

      <div class="group">
        <div class="group-title">系统工具</div>
        <div class="actions">
          <button class="primary task-btn" onclick="runTask('prepare_local')">首次完整准备</button>
          <button class="task-btn" onclick="runTask('readiness')">就绪检查</button>
          <button class="task-btn" onclick="runTask('formula')">导出东财公式</button>
        </div>
      </div>
    </aside>

    <main>
      <section class="card status-card">
        <div class="section-title">
          <b>运行状态</b>
          <span id="jobBadge" class="pill">无任务</span>
        </div>
        <div class="status-line">
          <span id="statusDot" class="status-dot"></span>
          <div class="status-main">
            <div id="status" class="status-text">终端已就绪。</div>
            <div class="progress-shell"><div id="progressBar" class="progress-bar"></div></div>
            <div id="progressHint" class="mini-note" style="margin-top:7px">等待任务。</div>
          </div>
        </div>
      </section>

      <section class="card">
        <div class="result-head">
          <div>
            <b>任务结果</b>
            <div class="mini-note">关键指标优先展示，明细表支持横向滚动。</div>
          </div>
          <span class="pill" id="resultKind">暂无结果</span>
        </div>
        <div id="summary"></div>
        <div id="result" class="empty">
          <div><strong>还没有任务结果</strong>从左侧选择扫描、回测或研究任务。</div>
        </div>
      </section>
    </main>
  </div>

  <section class="card reports-card">
    <div class="section-title">
      <div>
        <b>本地报告</b>
        <div class="mini-note">最近生成的 CSV / JSON 报告会显示在这里。</div>
      </div>
      <div class="report-head">
        <span class="pill" id="reportCount">0 个文件</span>
        <span class="pill" id="reportPath"></span>
      </div>
    </div>
    <div id="reports">加载中...</div>
  </section>
</div>

<script>
let activeJob=null,pollTimer=null,lastAction='';
const $=id=>document.getElementById(id);
const actionNames={
  prepare_local:'首次完整准备',readiness:'就绪检查',audit_data:'数据完整性审计',
  source_quality:'数据源质量追踪',repair_data:'定向修复失败',bootstrap:'更新本地数据库',
  scan:'全市场扫描',stock_detail:'单股详情',backtest:'V1真实回测',
  features:'特征有效性',ablation:'V1规则消融',select_features:'生成V2候选',walk_forward:'Walk-Forward',
  compare_models:'V1 / V2 对比',validate_system:'一键系统验收',
  optimize:'参数优化',formula:'导出东财公式'
};
function esc(v){return String(v??'').replace(/[&<>"']/g,s=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]))}
function compact(v){
  if(v===null||v===undefined||v==='')return '--';
  if(typeof v==='number')return Number.isInteger(v)?String(v):String(Math.round(v*1000)/1000);
  if(typeof v==='boolean')return v?'是':'否';
  if(typeof v==='object')return JSON.stringify(v);
  return String(v);
}
function statusClass(v){
  const s=String(v??'');
  if(/FAIL|失败|异常|未通过|错误/.test(s))return 'bad';
  if(/WARN|警告|观察|待|降级|部分/.test(s))return 'warn';
  if(/OK|通过|完成|就绪|成功/.test(s))return 'ok';
  return '';
}
function table(rows){
  if(!rows||!rows.length)return '<div class="empty" style="min-height:110px"><div>没有表格数据</div></div>';
  const keys=Object.keys(rows[0]);
  return '<div class="tablewrap"><table><thead><tr>'+keys.map(k=>'<th>'+esc(k)+'</th>').join('')+
    '</tr></thead><tbody>'+rows.map(r=>'<tr>'+keys.map(k=>{
      const v=r[k],cls=/状态|结果|风险/.test(k)?statusClass(v):'';
      return '<td class="'+cls+'">'+esc(compact(v))+'</td>';
    }).join('')+'</tr>').join('')+'</tbody></table></div>';
}
function summaryCards(o){
  if(!o||typeof o!=='object'||Array.isArray(o))return '';
  const entries=Object.entries(o);
  if(!entries.length)return '';
  return '<div class="summary-grid">'+entries.map(([k,v])=>
    '<div class="summary-item"><div class="sk">'+esc(k)+'</div><div class="sv '+statusClass(v)+'">'+esc(compact(v))+'</div></div>'
  ).join('')+'</div>';
}
function pretty(o){return '<pre>'+esc(JSON.stringify(o,null,2))+'</pre>'}
function payload(){
  let raw=$('limit').value.trim();
  return {
    limit:raw===''?null:Number(raw),
    refresh:$('refresh').checked,
    horizon:Number($('horizon').value),
    code:$('stockCode').value.trim()
  };
}
function setBusy(busy){
  document.querySelectorAll('.task-btn').forEach(b=>b.disabled=busy);
  $('taskState').textContent=busy?'运行中':'空闲';
  $('taskState').className='v '+(busy?'warn':'ok');
  $('taskHint').textContent=busy?'请等待当前任务结束':'可启动新任务';
}
function setProgress(message,status='running'){
  const text=String(message||'');
  const m=text.match(/(\d+)\s*\/\s*(\d+)/);
  let pct=0;
  if(m&&Number(m[2])>0)pct=Math.max(0,Math.min(100,Number(m[1])/Number(m[2])*100));
  else if(status==='completed')pct=100;
  $('progressBar').style.width=pct+'%';
  $('progressHint').textContent=m?('当前进度 '+m[1]+' / '+m[2]+' · '+pct.toFixed(1)+'%'):(status==='running'?'任务处理中…':'等待任务。');
  $('statusDot').className='status-dot '+(status==='failed'?'failed':status==='running'?'running':'');
}
async function runTask(action){
  if(action==='prepare_local'){
    const ok=confirm('首次完整准备会建立/更新沪深全市场本地数据库，并继续运行研究验收。首次执行可能耗时较长。确认开始吗？');
    if(!ok)return;
  }
  lastAction=action;
  setBusy(true);
  $('status').textContent='正在提交 '+(actionNames[action]||action)+'…';
  $('result').className='empty';
  $('result').innerHTML='<div><strong>任务已提交</strong>正在等待计算结果。</div>';
  $('summary').innerHTML='';
  $('resultKind').textContent=actionNames[action]||action;
  setProgress('', 'running');
  try{
    const r=await fetch('/api/run/'+action,{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(payload())
    });
    const d=await r.json();
    if(!d.ok){
      $('status').textContent=d.error||'启动失败';
      if(d.job_id){activeJob=d.job_id;startPolling()}else setBusy(false);
      return;
    }
    activeJob=d.job_id;
    $('jobBadge').textContent=(actionNames[action]||action)+' · '+activeJob;
    $('status').textContent='任务已启动，正在后台运行…';
    startPolling();
  }catch(err){
    $('status').textContent='请求失败：'+err;
    $('statusDot').className='status-dot failed';
    setBusy(false);
  }
}
function startPolling(){
  if(pollTimer)clearInterval(pollTimer);
  pollJob();
  pollTimer=setInterval(pollJob,1500);
}
async function pollJob(){
  if(!activeJob)return;
  try{
    const r=await fetch('/api/job/'+activeJob);
    const d=await r.json();
    if(!d.ok)return;
    const j=d.job;
    const action=j.action||lastAction;
    if(action)lastAction=action;
    $('status').textContent=(j.message||j.status||'运行中');
    $('jobBadge').textContent=(actionNames[action]||action||'任务')+' · '+activeJob;
    setProgress(j.message||'',j.status||'running');
    if(j.status==='completed'){
      clearInterval(pollTimer);pollTimer=null;
      $('status').innerHTML='<span class="ok">任务完成</span> · '+esc(j.finished_at||'');
      $('jobBadge').textContent=(actionNames[action]||action||'任务')+' · 已完成';
      renderResult(j.result||{},action);
      setProgress('', 'completed');
      setBusy(false);
      activeJob=null;
      refreshStatus();
    }else if(j.status==='failed'){
      clearInterval(pollTimer);pollTimer=null;
      $('status').innerHTML='<span class="bad">任务失败：</span> '+esc(j.error||j.message||'');
      $('result').className='';
      $('result').innerHTML=pretty({error:j.error,traceback:j.traceback});
      $('jobBadge').textContent=(actionNames[action]||action||'任务')+' · 失败';
      setProgress('', 'failed');
      setBusy(false);
      activeJob=null;
    }
  }catch(err){
    $('status').textContent='状态轮询暂时失败：'+err;
  }
}
function equityChart(rows){
  if(!rows||rows.length<2)return '';
  const keys=Object.keys(rows[0]||{});
  const key=keys.find(k=>/权益|净值|equity/i.test(k));
  if(!key)return '';
  const vals=rows.map(r=>Number(r[key])).filter(Number.isFinite);
  if(vals.length<2)return '';
  const min=Math.min(...vals),max=Math.max(...vals),span=max-min||1;
  const w=900,h=130,p=10;
  const pts=vals.map((v,i)=>{
    const x=p+i*(w-2*p)/(vals.length-1);
    const y=h-p-(v-min)*(h-2*p)/span;
    return x.toFixed(1)+','+y.toFixed(1);
  }).join(' ');
  return '<div class="block-title">近期组合权益</div><div class="chart"><svg viewBox="0 0 '+w+' '+h+'" width="100%" height="100%" preserveAspectRatio="none"><polyline fill="none" stroke="#6d9cff" stroke-width="3" points="'+pts+'"/></svg></div>';
}
function renderResult(r,action=''){
  $('result').className='';
  $('resultKind').textContent=actionNames[action]||action||'结果';
  let blocks=[];
  if(r.summary){
    $('summary').innerHTML=summaryCards(r.summary);
  }else if(r.metrics){
    $('summary').innerHTML=summaryCards(r.metrics);
  }else{
    $('summary').innerHTML='';
  }
  if(r.metrics)blocks.push('<div class="block-title">交易统计</div>'+summaryCards(r.metrics));
  if(r.portfolio)blocks.push('<div class="block-title">有限资金组合</div>'+summaryCards(r.portfolio));
  if(r.profile)blocks.push('<div class="block-title">策略参数</div>'+summaryCards(r.profile));
  if(r.candidate)blocks.push('<div class="block-title">V2候选模型</div>'+pretty(r.candidate));
  if(r.equity_tail&&r.equity_tail.length)blocks.push(equityChart(r.equity_tail)+'<div class="block-title">近期权益明细</div>'+table(r.equity_tail));
  if(r.rows&&r.rows.length)blocks.push('<div class="block-title">结果明细</div>'+table(r.rows));
  if(r.ablation_detail&&r.ablation_detail.length)blocks.push('<div class="block-title">各预测窗口消融明细</div>'+table(r.ablation_detail));
  if(r.usage&&r.usage.length)blocks.push('<div class="block-title">当前来源使用</div>'+table(r.usage));
  if(r.crosscheck&&r.crosscheck.length)blocks.push('<div class="block-title">多源差异</div>'+table(r.crosscheck));
  if(r.by_regime&&r.by_regime.length)blocks.push('<div class="block-title">市场环境拆分</div>'+table(r.by_regime));
  if(r.trades&&r.trades.length)blocks.push('<div class="block-title">交易样本</div>'+table(r.trades));
  $('result').innerHTML=blocks.join('')||pretty(r);
}
async function refreshStatus(){
  try{
    const r=await fetch('/api/status');
    const d=await r.json();
    if(!d.ok)return;
    $('runtime').textContent=d.runtime.name;
    $('memory').textContent=d.memory.available_gb+' GB';
    $('threshold').textContent=d.profile.score_threshold;
    $('modelState').textContent=d.models.active_model+' / '+d.models.v2_state;
    $('reportPath').textContent=d.paths.reports;
    $('reportCount').textContent=(d.reports||[]).length+' 个文件';
    $('reports').innerHTML=table(d.reports||[]);
    if(d.active_job&&!activeJob){
      activeJob=d.active_job;
      setBusy(true);
      $('jobBadge').textContent='恢复运行中任务 · '+activeJob;
      startPolling();
    }else if(!d.active_job&&!activeJob){
      setBusy(false);
    }
  }catch(err){
    $('reports').innerHTML='<span class="bad">状态读取失败：'+esc(err)+'</span>';
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
        "source_quality",
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
        "ablation",
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
