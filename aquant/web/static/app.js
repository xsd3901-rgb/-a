let activeJob=null,pollTimer=null,lastAction='';
const $=id=>document.getElementById(id);
const actionNames={
  prepare_local:'首次完整准备',readiness:'就绪检查',audit_data:'数据完整性审计',
  refresh_reference:'更新基础资料',
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
  if(/FAIL|失败|异常|未通过|错误|cooldown/.test(s))return 'bad';
  if(/WARN|警告|观察|待|降级|部分|degraded/.test(s))return 'warn';
  if(/OK|通过|完成|就绪|成功|healthy/.test(s))return 'ok';
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
  if(r.source_health&&r.source_health.length)blocks.push('<div class="block-title">数据源健康</div>'+table(r.source_health));
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
    const dc=d.data_center||{};
    const dcSummary=dc.summary||{};
    $('dataCenterSummary').innerHTML=summaryCards(dcSummary);
    $('sourceHealth').innerHTML=table(dc.source_health||[]);
    const cooling=Number(dcSummary['冷却数据源']||0);
    $('dataCenterState').textContent=cooling>0?('有 '+cooling+' 个源冷却中'):'本地数据层正常';
    $('dataCenterState').className='pill '+(cooling>0?'warn':'ok');
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
