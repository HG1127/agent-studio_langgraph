"use client";
import {useCallback,useEffect,useState} from "react";
const BASE=process.env.NEXT_PUBLIC_API_BASE_URL||"http://127.0.0.1:8000";
type Spec={schema_version:string;name:string;description:string;goal:{task:string;success_criteria:string[]};tools:{enabled:string[]};[key:string]:unknown};
type Agent={id:string;spec:Spec;created_at:string};
type Run={id:string;agent_id:string;status:string;input:string;output:string|null;error:string|null;created_at:string};
type Event={seq:number;type:string;payload:Record<string,unknown>;created_at:string};
type Health={status:string;model_key_configured:boolean;authentication_available:boolean};
type Tool={id:string;description:string;risk:string};
type Page="create"|"agents"|"runs"|"tools";
async function api<T>(path:string,options?:RequestInit):Promise<T>{
 const resp=await fetch(BASE+path,{...options,headers:{"Content-Type":"application/json",...options?.headers},cache:"no-store"});
 const raw=await resp.text();let data:unknown={};try{data=raw?JSON.parse(raw):{}}catch{data={detail:raw}}
 if(!resp.ok){const detail=(data as {detail?:unknown}).detail;throw new Error(typeof detail==="string"?detail:"请求失败（"+resp.status+"）")}
 return data as T;
}
const templates=[
 {icon:"◈",name:"研究助手",detail:"整理问题、查找证据、生成报告",prompt:"创建一个研究助手，针对用户给定的研究目标，计划分析步骤，基于获授权信息回答，并明确区分事实和推测。"},
 {icon:"▦",name:"数据分析助手",detail:"计算、核对并解释数据",prompt:"创建一个数据分析助手，能够用已授权的计算工具核对数字，说明计算过程，输出清晰的分析结论。"},
 {icon:"✉",name:"办公助手",detail:"组织任务、准备内容草稿",prompt:"创建一个办公助手，根据用户给定文本整理待办、工作计划与邮件草稿，涉及对外操作时必须获得批准。"}
];
const capabilities=[
 ["Goal / Task","任务目标与成功标准","已建模"],["Context","上下文来源与引用","基础配置"],
 ["Tools","工具注册与使用","本地工具"],["State","状态与运行事件","基础记录"],
 ["Memory","受控长期记忆","待开发"],["Planning","自主规划","基础配置"],
 ["Tool Calling","真实结构化调用","已接入"],["Permissions","权限与审批","部分实现"],
 ["Environment","沙盒与工作环境","待开发"],["Observability","执行轨迹","基础追踪"],
 ["Evaluation","测试与发布门槛","待开发"],["Stop Condition","限制和终止规则","部分实现"]
];
function manualSpec(task:string):Spec{
 return {schema_version:"1.0",name:"我的自定义助手",description:"手动创建的配置草稿（并非 AI 生成）",execution_mode:"autonomous",
 goal:{task,success_criteria:["提供明确结果，说明没有证据支持的部分"]},
 context:{sources:[],require_citations:false},tools:{enabled:["utc_now","add_numbers"]},
 state:{persistence:"event_log_development",resume_enabled:false},memory:{enabled:false,scope:"none"},
 planning:{enabled:true,max_replans:1},tool_calling:{structured_arguments:true,retry_limit:1},
 permissions:{allowed_tools:["utc_now","add_numbers"],external_write_allowed:false},
 environment:{profile:"local_read_only"},observability:{trace_enabled:true,redact_secrets:true},
 evaluation:{suite_id:null,enforced:false},stop_conditions:{max_model_rounds:8,max_tool_calls:10,max_duration_seconds:120,max_no_progress_steps:3}};
}
const statusLabel:Record<string,string>={QUEUED:"等待执行",RUNNING:"执行中",SUCCEEDED:"执行完成",PARTIAL:"部分完成",FAILED:"执行失败",CANCELED:"已取消"};
export default function Home(){
 const [page,setPage]=useState<Page>("create"),[agents,setAgents]=useState<Agent[]>([]),[runs,setRuns]=useState<Run[]>([]),
 [tools,setTools]=useState<Tool[]>([]),[health,setHealth]=useState<Health|null>(null),[connected,setConnected]=useState(false);
 const [description,setDescription]=useState(""),[draft,setDraft]=useState<Spec|null>(null),[working,setWorking]=useState(false);
 const [selectedAgent,setSelectedAgent]=useState<string|null>(null),[selectedRun,setSelectedRun]=useState<string|null>(null);
 const [task,setTask]=useState(""),[events,setEvents]=useState<Event[]>([]);
 const [feedback,setFeedback]=useState<{error:boolean;text:string}|null>(null);
 const refresh=useCallback(async()=>{try{const [h,a,r,t]=await Promise.all([api<Health>("/v1/health"),api<Agent[]>("/v1/agents"),api<Run[]>("/v1/runs"),api<Tool[]>("/v1/tools")]);setHealth(h);setAgents(a);setRuns(r);setTools(t);setConnected(true)}catch{setConnected(false)}},[]);
 const refreshRun=useCallback(async(id:string)=>{try{const [r,e]=await Promise.all([api<Run>("/v1/runs/"+id),api<Event[]>("/v1/runs/"+id+"/events")]);setRuns(old=>[r,...old.filter(x=>x.id!==r.id)]);setEvents(e)}catch{/* backend connection indicator appears on refresh */}},[]);
 useEffect(()=>{void refresh()},[refresh]);
 useEffect(()=>{if(!selectedRun)return;void refreshRun(selectedRun);const timer=window.setInterval(()=>void refreshRun(selectedRun),2400);return()=>window.clearInterval(timer)},[selectedRun,refreshRun]);
 async function prepare(ai:boolean){
  if(description.trim().length<5){setFeedback({error:true,text:"请至少用 5 个字描述 Agent 的目标。"});return}
  setWorking(true);setFeedback(null);
  try{const result=ai?(await api<{spec:Spec}>("/v1/agents/drafts/generate",{method:"POST",body:JSON.stringify({description})})).spec:manualSpec(description.trim());
   setDraft(result);setFeedback({error:false,text:ai?"AI 已生成结构化草稿，请审查后保存。":"手动生成配置模板；并未调用 AI。"});
  }catch(e){setFeedback({error:true,text:e instanceof Error?e.message:"创建失败"})}finally{setWorking(false)}
 }
 async function save(){
  if(!draft)return;setWorking(true);
  try{const agent=await api<Agent>("/v1/agents",{method:"POST",body:JSON.stringify(draft)});
   await refresh();setSelectedAgent(agent.id);setDraft(null);setPage("agents");setFeedback({error:false,text:"已保存 Agent 配置；当前只有本地只读工具可执行。"});
  }catch(e){setFeedback({error:true,text:e instanceof Error?e.message:"保存失败"})}finally{setWorking(false)}
 }
 async function run(){
  if(!selectedAgent||!task.trim()){setFeedback({error:true,text:"请先选择 Agent 并填写本次任务。"});return}
  setWorking(true);
  try{const result=await api<Run>("/v1/agents/"+selectedAgent+"/runs",{method:"POST",body:JSON.stringify({input:task})});
   setSelectedRun(result.id);setPage("runs");setEvents([]);await refresh();setFeedback({error:false,text:"已提交真实执行任务。未配置模型时，后台会明确记录失败。"});
  }catch(e){setFeedback({error:true,text:e instanceof Error?e.message:"执行失败"})}finally{setWorking(false)}
 }
 const nav:[Page,string,string][]=[["create","AI 创建中心","✦"],["agents","我的 Agents","◉"],["runs","运行中心","▤"],["tools","工具与能力","⌘"]];
 const agent=agents.find(a=>a.id===selectedAgent),current=runs.find(r=>r.id===selectedRun);
 return <div className="shell">
  <aside className="sidebar"><div className="brand"><span className="brand-icon">✦</span><div><strong>Agent Studio</strong><small>Build · Run · Improve</small></div></div>
   <div className="nav-head">WORKSPACE</div><nav>{nav.map(([key,title,icon])=><button key={key} className={"nav "+(page===key?"active":"")} onClick={()=>{setPage(key);setFeedback(null)}}><span>{icon}</span>{title}</button>)}</nav>
   <div className="side-bottom"><span className={"signal "+(connected?"good":"bad")}/> {connected?"API 已连接":"API 未连接"}<small>本地开发样板 · v0.1</small></div>
  </aside>
  <main className="main"><header className="topbar"><span>Workspace <b>/</b> {nav.find(x=>x[0]===page)?.[1]}</span><div><span className="pill">开发预览</span><span className="user">AS</span></div></header>
   <div className="container">
    <div className="notice"><strong>本地验证版</strong> · 尚未实现用户登录、多租户隔离、可靠任务恢复及沙盒。请勿公开部署或导入敏感资料。</div>
    {feedback&&<div className={"feedback "+(feedback.error?"error":"ok")} role="status">{feedback.text}<button aria-label="关闭提示" onClick={()=>setFeedback(null)}>×</button></div>}
    {page==="create"&&<>
     <section className="hero"><div className="eyebrow">AI-NATIVE AGENT BUILDER</div><h1>一句话，创建你的 <em>AI 助手</em></h1>
      <p>不用理解复杂 Prompt 或流程节点。描述想完成的工作，系统将生成结构化配置，并通过真实任务检验。</p>
      <div className="composer"><textarea aria-label="描述 Agent 目标" placeholder="例如：建立一个全球人物调研助手，梳理背景、商业关系和可核验来源……" value={description} onChange={e=>setDescription(e.target.value)}/>
       <div className="composer-actions"><span>目标 · 工具 · 记忆 · 执行规则</span><div><button className="btn secondary" onClick={()=>void prepare(false)} disabled={working}>手动配置草稿</button><button className="btn primary" onClick={()=>void prepare(true)} disabled={working}>{working?"处理中…":"✦ AI 生成草案"}</button></div></div>
      </div>
      {!health?.model_key_configured&&<small className="hint">尚未连接模型。AI 生成与真实执行需要服务端 MODEL_API_KEY；手动草稿可用于验证配置保存。</small>}
     </section>
     {draft&&<section className="card preview"><div className="section-heading"><h2>Agent 配置草稿</h2><span className="pill">AgentSpec v{draft.schema_version}</span></div>
       <div className="spec-lines"><p><label>名称</label><strong>{draft.name}</strong></p><p><label>任务目标</label><strong>{draft.goal.task}</strong></p><p><label>允许工具</label><strong>{draft.tools.enabled.join("、")||"无"}</strong></p></div>
       <div className="actions"><button className="btn secondary" onClick={()=>setDraft(null)}>取消</button><button className="btn primary" onClick={()=>void save()} disabled={working}>保存 Agent</button></div>
     </section>}
     <div className="section-heading"><div><div className="eyebrow">GET STARTED</div><h2>从具体工作场景开始</h2></div><small>模板仅填入需求，不代表工具已授权</small></div>
     <div className="templates">{templates.map(t=><button className="template" key={t.name} onClick={()=>{setDescription(t.prompt);setDraft(null)}}><span className="template-icon">{t.icon}</span><strong>{t.name}</strong><small>{t.detail}</small><b>使用该需求 →</b></button>)}</div>
     <div className="section-heading spaced"><div><div className="eyebrow">AGENT RUNTIME</div><h2>Agent 的 12 项核心能力</h2></div><small>展示的是当前工程进度，而非生产验收</small></div>
     <div className="capabilities">{capabilities.map(([key,detail,phase],idx)=><div className="capability" key={key}><span>{String(idx+1).padStart(2,"0")}</span><div><strong>{key}</strong><small>{detail}</small></div><i>{phase}</i></div>)}</div>
    </>}
    {page==="agents"&&<><div className="section-heading title"><div><div className="eyebrow">YOUR ASSISTANTS</div><h1>我的 Agents</h1><p>管理已保存的助手配置并发起真实执行</p></div><button className="btn primary" onClick={()=>setPage("create")}>+ 新建 Agent</button></div>
     {!agents.length&&<div className="empty">还没有 Agent。请先进入创建中心，保存一份配置。</div>}
     <div className="agent-grid">{agents.map(a=><button className={"agent "+(selectedAgent===a.id?"selected":"")} key={a.id} onClick={()=>setSelectedAgent(a.id)}><span className="template-icon">✦</span><strong>{a.spec.name}</strong><small>{a.spec.goal.task}</small><b>已配置 {a.spec.tools.enabled.length} 个本地工具</b></button>)}</div>
     {agent&&<section className="card spaced"><div className="section-heading"><h2>{agent.spec.name} · 执行工作台</h2><span className="pill">只读工具</span></div><p>{agent.spec.goal.task}</p><label className="label" htmlFor="run-input">本次任务</label><textarea className="task-input" id="run-input" placeholder="例如：请调用工具计算 120 + 350。" value={task} onChange={e=>setTask(e.target.value)}/><div className="actions"><button className="btn primary" disabled={working} onClick={()=>void run()}>▷ 开始真实执行</button></div><p className="hint">目前仅有 UTC 时间和数字加法工具。未接入模型则真实执行失败，不会生成虚假的研究结果。</p></section>}
    </>}
    {page==="runs"&&<><div className="section-heading title"><div><div className="eyebrow">RUN MONITOR</div><h1>运行中心</h1><p>任务状态、真实工具调用与错误记录</p></div><button className="btn secondary" onClick={()=>void refresh()}>刷新 ↻</button></div>
     <div className="run-grid"><section className="card"><h3>最近的任务</h3>{!runs.length&&<div className="empty">暂无任务</div>}
       {runs.map(r=><button className={"run-item "+(selectedRun===r.id?"selected":"")} key={r.id} onClick={()=>setSelectedRun(r.id)}><strong>{r.input}</strong><small><span className={"status "+r.status.toLowerCase()}>{statusLabel[r.status]||r.status}</span> {new Date(r.created_at).toLocaleString("zh-CN")}</small></button>)}</section>
       <section className="card">{current?<><div className="section-heading"><h3>任务详情</h3><span className={"status "+current.status.toLowerCase()}>{statusLabel[current.status]||current.status}</span></div><p className="task-quote">{current.input}</p>{current.error&&<p className="error-box">{current.error}</p>}{current.output&&<div className="result"><h3>模型返回的结果</h3><p>{current.output}</p></div>}<h3>执行时间线 · {events.length} 条事件</h3><div className="timeline">{events.map(e=><div key={e.seq} className="event"><b>{e.type}</b><time>{new Date(e.created_at).toLocaleTimeString("zh-CN")}</time><pre>{JSON.stringify(e.payload,null,2)}</pre></div>)}</div></>:<div className="empty">点击左侧任务查看详情</div>}</section></div>
    </>}
    {page==="tools"&&<><div className="section-heading title"><div><div className="eyebrow">TOOL REGISTRY</div><h1>工具与能力中心</h1><p>所有工具需注册并通过服务端权限校验</p></div><span className="pill">本地只读工具</span></div><div className="tool-grid">{tools.map(t=><section className="card" key={t.id}><span className="template-icon">⌘</span><h3>{t.id}</h3><p>{t.description}</p><span className="pill">{t.risk==="read_only"?"只读":t.risk}</span></section>)}</div><section className="card spaced"><h2>安全与能力边界</h2><p>本版具备工具 allowlist 和结构化参数校验，但正式认证、MCP 连接器、人工审批、沙盒与可靠恢复都还在开发计划中。服务端已设置生产模式启动阻断。</p><div className="todo-grid">{["正式身份认证与租户隔离","MCP / OAuth 连接器","人工审批与独立工具沙盒","LangGraph 检查点与 Worker"].map(s=><div key={s}><b>待开发</b><span>{s}</span></div>)}</div></section></>}
   </div>
  </main>
 </div>;
}
