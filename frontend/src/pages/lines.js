import {empty, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {workspace} from '../stores/workspace.js';
import {el, h, link, p} from '../utils/dom.js';
import {date} from '../utils/format.js';

const CLOSED = {'有可用方案':'ok','待核':'pending','已停线':'stopped'};

function linePanel(line) {
  const combos = table([
    {label:'最小停线组合',render:row=>el('strong',{},row.instruments.join(' + '))},
    {label:'堵住的方案',render:row=>row.plans.map(plan=>`${plan.plan_code} ${plan.plan_name}`).join('；') || '—'},
  ],line.combos,{caption:`${line.line_name} 最小停线组合`,empty:'没有可列出的最小停线组合。',pageSize:6});
  const blocked = table([
    {key:'plan_code',label:'方案编号'},{key:'plan_name',label:'方案名称'},
    {label:'方案器具',render:row=>row.instruments.join(' + ')},
    {label:'说明',render:row=>row.reasons.join('；') || '—'},
  ],line.plans.filter(plan=>plan.state !== '可用'),{caption:`${line.line_name} 不可用与待核方案`,empty:'所有方案均可用。',pageSize:6});
  return panel(`${line.line_code} ${line.line_name}`,
    el('div',{class:'actions'},pill(line.status,CLOSED[line.status] || line.status),
      p(`可用方案 ${line.usable_count} · 不可用 ${line.blocked_count} · 待核 ${line.pending_count}`,'subtle')),
    p(line.note,'subtle'),
    line.combos_truncated && p('可用方案过多，仅列出最小的 60 组停线组合。','subtle'),
    combos, blocked);
}

export async function linesPage() {
  const {spec} = workspace;
  const report = await workspace.report();
  if (!report) return panel(spec.pages[4], empty('还没有已发布的停线班报，完成一次运行后在此查看。','#run','前往运行'));
  const result = report.result;
  const lines = result.lines || [];
  return el('div',{},
    el('section',{class:'panel result-head','data-testid':'line-report'},
      p(`依据批次 ${report.source ? report.source.name : report.dataset_id} · 运行 ${report.run_id}`,'eyeline'),
      h(2,result.headline), p(result.note,'intro-copy'),
      el('div',{class:'actions'},link('查看计算证据',`#results/${report.run_id}`,{class:'quiet'}),
        p(`发布时间 ${date(report.published_at)}`,'subtle'))),
    el('div',{class:'lines-grid'},lines.map(linePanel)));
}
