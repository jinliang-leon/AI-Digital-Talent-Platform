const root = document.querySelector("#app");
const modal = document.querySelector("#dialog");
const notice = document.querySelector("#notice");
const names = {
  admin: "系统管理员", hr: "HR", interviewer: "面试官", open: "在招", closed: "已关闭",
  collected: "已收录", pending_review: "待审核", pending_interview: "待面试",
  interviewing: "面试中", pending_decision: "待人工决策", hired: "已录用", rejected: "未录用", withdrawn: "候选人放弃", reserve: "人才储备",
};
const menu = {hr: ["home", "jobs", "candidates", "resumes", "talent", "applications", "knowledge", "chat"], interviewer: ["applications", "jobs", "knowledge", "chat"], admin: ["users", "audit"]};
const titles = {home: "招聘工作台", jobs: "招聘岗位", candidates: "候选人人才库", applications: "应聘与面试", users: "用户与权限", audit: "操作审计",
  resumes:"简历解析",talent:"人才检索",knowledge:"招聘知识库",chat:"招聘助手"};
const stages = {
  collected: ["pending_review"], pending_review: ["pending_interview", "rejected", "withdrawn", "reserve"],
  pending_interview: ["interviewing", "pending_review", "withdrawn"], interviewing: ["withdrawn"],
  pending_decision: ["hired", "rejected", "withdrawn", "reserve", "pending_interview"], reserve: ["pending_review"],
};
let token = sessionStorage.getItem("talent-token"), user, page, offset = 0, query = "", renderId = 0, timer;
const pageSize = 20;

function e(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs ?? {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (["checked", "selected", "disabled", "value"].includes(key)) node[key] = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  node.append(...children.flat(Infinity).filter(child => child !== null && child !== undefined));
  return node;
}
const d = (css, ...children) => e("div", {class: css}, ...children);
const txt = (value) => value || "—";
const label = (key) => names[key] || key;
const badge = (key) => e("span", {class: "pill " + (["open", "hired"].includes(key) ? "active" : key?.startsWith("pending") ? "attention" : "")}, label(key));
const date = (value) => new Date(/Z$|[+-]\d\d:\d\d$/.test(value) ? value : value + "Z").toLocaleString("zh-CN");
function flash(message) {
  notice.textContent = message;
  clearTimeout(timer);
  timer = setTimeout(() => { notice.textContent = ""; }, 3500);
}
function b(text, action, css = "", disabled = false) {
  return e("button", {type: "button", class: "button " + css, disabled, onclick: async () => {
    try { await action(); } catch (err) { flash(err.message); }
  }}, text);
}
const submit = (value) => e("button", {type: "submit", class: "button primary"}, value);
const params = (value) => new URLSearchParams(value).toString();
const post = (url, value) => api(url, "POST", value);
const patch = (url, value) => api(url, "PATCH", value);
async function api(path, method = "GET", body, raw = false) {
  const headers = new Headers(token ? {Authorization: "Bearer " + token} : {});
  const binary = body instanceof Blob;
  if (body !== undefined) headers.set("Content-Type", binary ? "application/octet-stream" : "application/json");
  const res = await fetch("/api/v1" + path, {method, headers, body: binary ? body : JSON.stringify(body)});
  if (res.status === 401 && user) { logoutUI("登录已失效，请重新登录。"); throw Error("登录已失效"); }
  if (!res.ok) throw Error((await res.json().catch(() => ({}))).message || "请求失败（" + res.status + "）");
  if (raw) return res;
  if (res.status !== 204) return res.json();
}
function brand() {
  return d("brand", d("brand-mark", "T"), d("", e("strong", {}, "AI 数字化人才平台"), e("small", {}, "Talent Hub")));
}
function logoutUI(message = "") {
  token = null; user = null; ++renderId;
  sessionStorage.removeItem("talent-token");
  if (modal.open) modal.close();
  loginPage(message);
}
function loginPage(message = "") {
  const error = d("form-error", message), button = submit("登录");
  const form = e("form", {class: "panel", onsubmit: async event => {
    event.preventDefault(); button.disabled = true; error.textContent = "";
    try {
      const data = await post("/auth/login", Object.fromEntries(new FormData(form)));
      token = data.access_token;
      sessionStorage.setItem("talent-token", token);
      enter(data.user);
    } catch (err) { error.textContent = err.message; }
    finally { button.disabled = false; }
  }}, e("h2", {}, "登录工作台"), field("username","账号","",{required:true, autocomplete:"username"}),
    field("password","密码","",{type:"password",required:true,autocomplete:"current-password"}), error, button);
  root.replaceChildren(e("main", {class: "login"},
    e("section", {class: "login-brand"}, brand(), e("h1", {}, "招聘工作台"), e("p", {}, "统一处理招聘岗位、候选人和面试记录。")),
    e("section", {class: "login-main"}, form)));
}
function enter(profile) { user = profile; navigate(menu[user.role][0]); }
function navigate(next) { page = next; offset = 0; query = ""; refresh(); }
async function refresh() {
  const id = ++renderId, current = page, region = d("", d("empty", "正在加载…"));
  root.replaceChildren(d("shell", e("aside", {class: "sidebar"}, brand(),
    e("nav", {class: "nav", "aria-label": "招聘导航"}, menu[user.role].filter(key=>key!=="resumes"||user.view_contacts).map(key =>
      e("button", {type:"button", "aria-current": current === key ? "page" : null, onclick: () => navigate(key)}, titles[key])))),
    d("", e("header", {class:"topbar"}, e("strong",{},"招聘管理"),d("account",user.name,badge(user.role),
      b("退出登录", async()=>{ await post("/auth/logout"); logoutUI(); }))),
      e("main",{class:"content"},region))));
  try {
    const view = await render[current]();
    if (renderId === id) region.replaceChildren(view);
  } catch (err) {
    if (renderId === id) region.replaceChildren(d("error", err.message), b("重新加载", refresh));
  }
}
function table(headings, rows) {
  if (!rows.length) return d("empty", "暂无记录");
  return d("table-wrap", e("table", {}, e("thead",{},e("tr",{},headings.map(h=> e("th",{scope:"col"},h)))),
    e("tbody",{},rows.map(row=>e("tr",{},row.map(cell=>cell?.tagName === "TD" ? cell : e("td",{},cell ?? "—")))))));
}
const note = (value) => e("td", {class:"note"}, txt(value));
const panel = (title, body, action) => e("section",{class:"panel"},d("panel-header",e("h2",{},title),action),body);
const heading = (title, actions) => d("heading",e("h1",{},title),actions);
const pager = (total) => d("pager",e("small",{},"共 " + total + " 条"),
  b("上一页",()=>{offset-=pageSize;refresh();},"",offset === 0),
  b("下一页",()=>{offset+=pageSize;refresh();},"",offset + pageSize >= total));
function search(placeholder) {
  const input = e("input",{value:query,placeholder,"aria-label":placeholder,maxlength:100});
  return e("form",{class:"filters",onsubmit:event=>{event.preventDefault();query=input.value.trim();offset=0;refresh();}},
    input, e("button",{class:"button",type:"submit"},"搜索"));
}
function field(key, title, val = "", options = {}) {
  const {type = "text", wide = false, max = 160, required = false, autocomplete = "off"} = options;
  const props = {name:key,maxlength:max,required};
  return e("label",{class:wide ? "wide" : ""},title,type === "textarea" ? e("textarea",props,val ?? "") :
    e("input",{...props,type,value:val ?? "",min:type === "number" ? 1 : null,autocomplete}));
}
function check(name, title, checked = false) {
  return e("label",{class:"check wide"},e("input",{type:"checkbox",name,checked}),title);
}
function choices(key, title, options, value) {
  const select = e("select",{name:key},options.map(([id,text])=>e("option",{value:id},text)));
  select.value = value ?? options[0]?.[0] ?? "";
  return e("label",{},title,select);
}
function showModal(title, body, footer) {
  modal.replaceChildren(d("panel-header",e("h2",{id:"dialog-title"},title),b("关闭",()=>modal.close())),
    ...[body,footer].filter(Boolean));
  if (!modal.open) modal.showModal();
}
function formModal(title, fields, save, caption = "保存") {
  const error = d("form-error"), primary = submit(caption);
  const form = e("form",{onsubmit: async(event)=>{
    event.preventDefault(); primary.disabled=true; error.textContent="";
    try {
      const data = new FormData(form);
      const afterSave = await save(data,Object.fromEntries(data));
      modal.close();
      flash("操作已保存");
      refresh();
      if (typeof afterSave === "function") await afterSave();
    }
    catch (err) { error.textContent=err.message; } finally { primary.disabled=false; }
  }},d("panel-body",d("fields",fields),error),d("dialog-footer",b("取消",()=>modal.close()),primary));
  showModal(title,form);
}
const detail = (pairs) => e("dl",{class:"details"},pairs.flatMap(([key,value])=>[e("dt",{},key),e("dd",{},txt(value))]));
const listParams = () => params({q:query,offset,limit:pageSize});

function jobForm(job) {
  const fields = [
    ["title","岗位名称","text",true,160],["department","需求部门","text",true,100],["headcount","招聘人数","number",true,4],
    ["responsibilities","岗位职责","textarea",true,800],["required_skills","必备技能","textarea",true,800],
    ["preferred_skills","优先技能","textarea",false,8000],["experience","经验要求","textarea",false,8000],
  ];
  formModal(job ? "编辑招聘岗位" : "新建招聘岗位",[
    fields.map(([key,title,type,required,max])=>field(key,title,job?.[key] ?? (key==="headcount" ? 1 : ""),{type,max,required,wide:type==="textarea"})),
    check("synthetic","标记为模拟数据",job?.synthetic),
  ],(data,body)=>{
    body.headcount=Number(body.headcount); body.synthetic=data.has("synthetic");
    return job ? patch("/jobs/"+job.id,{...body,expected_version:job.version}) : post("/jobs",body);
  });
}
function reason(title, handler) {
  formModal(title,[field("reason","处理原因","",{required:true,type:"textarea",wide:true,max:800})],(_,value)=>handler(value.reason),"确认");
}
function jobDetail(job) {
  showModal(job.title,d("dialog-details",detail([["需求部门",job.department],["招聘人数",String(job.headcount)],
    ["状态",badge(job.status)],["岗位职责",job.responsibilities],["必备技能",job.required_skills],
    ["优先技能",job.preferred_skills],["经验要求",job.experience]])));
}
async function jobs() {
  const data = await api("/jobs?"+listParams());
  const hr=user.role==="hr";
  return d("",heading(titles.jobs,hr ? b("新建岗位",()=>jobForm(),"primary") : null),
    panel("岗位列表",d("",table(["岗位名称","部门","人数","状态","操作"],data.items.map(job=>[
      b(job.title,()=>jobDetail(job),"link"),job.department,String(job.headcount),d("actions",badge(job.status),job.synthetic?badge("模拟"):null),
      d("actions",hr?b("编辑",()=>jobForm(job)):null,hr&&job.status==="open"?b("关闭岗位",()=>reason("关闭岗位",why=>
        post("/jobs/"+job.id+"/close",{expected_version:job.version,reason:why})),"warn"):null),
    ])),pager(data.total)),search("搜索岗位名称")));
}
async function candidateForm(item) {
  const path = "/candidates" + (item ? "/"+item.id : "");
  const contact = item && user.view_contacts ? (await api(path+"/contact")).contact : null;
  formModal(item?"编辑候选人":"新建候选人",[
    field("name","姓名",item?.name,{required:true,max:100}),field("headline","职业方向",item?.headline),
    field("skills","技能与经历概述",item?.skills,{type:"textarea",wide:true,max:8000}),
    user.view_contacts?field("email","邮箱",contact?.email,{type:"email",max:254}):null,
    user.view_contacts?field("phone","电话",contact?.phone,{max:40}):null,
    check("synthetic","标记为模拟数据",item?.synthetic),
  ],(form,body)=>{
    const {email,phone,...candidate}=body;
    candidate.synthetic=form.has("synthetic");
    if (user.view_contacts&&(contact||email||phone)) candidate.contact={email,phone};
    return item?patch(path,{...candidate,expected_version:item.version}):post(path,candidate);
  });
}
function upload(item) {
  formModal("上传简历",[
    e("label",{class:"wide"},"简历文件",e("input",{name:"file",type:"file",required:true,accept:".pdf,.docx,.txt,.jpg,.jpeg,.png"})),
    e("small",{class:"wide"},"PDF、DOCX、TXT、JPG、PNG，每份最多 20 MiB。"),
    user.view_contacts?check("interviewer_visible","已人工确认脱敏，可供已指派的面试官下载"):null,
  ],(data)=>{
    const file=data.get("file");
    return post("/candidates/"+item.id+"/files?"+params({filename:file.name,interviewer_visible:data.has("interviewer_visible")}),file);
  },"上传");
}
async function download(file) {
  const result = await api("/files/"+file.id+"/download","GET",undefined,true);
  const url=URL.createObjectURL(await result.blob());
  e("a",{href:url,download:file.filename}).click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function candidateDetail(id) {
  const path="/candidates/"+id;
  const [item,files,contactData]=await Promise.all([api(path),api(path+"/files"),user.view_contacts?api(path+"/contact"):null]);
  const contact=contactData?.contact;
  showModal(item.name,d("dialog-details",detail([["职业方向",item.headline],["技能与经历",item.skills],
    ...(user.view_contacts?[["邮箱",contact?.email],["电话",contact?.phone]]:[])]),
    d("actions",e("h3",{},"简历文件"),user.role==="hr"?b("上传简历",()=>upload(item),"link"):null),
    table(["文件名","范围","操作"],files.map(file=>[file.filename,file.interviewer_visible?"脱敏副本":"敏感原件",d("actions",b("下载",()=>download(file)),
      user.view_contacts&&!file.interviewer_visible?b("解析",async()=>{
        await post("/files/"+file.id+"/parse",{synthetic:item.synthetic});modal.close();navigate("resumes");
      }):null)]))));
}
async function candidates() {
  const data=await api("/candidates?"+listParams());
  return d("",heading(titles.candidates,b("新建候选人",()=>candidateForm(),"primary")),
    panel("候选人列表",d("",table(["候选人","职业方向","技能与经历","操作"],data.items.map(item=>[
      d("actions",b(item.name,()=>candidateDetail(item.id),"link"),item.synthetic?badge("模拟"):null),
      txt(item.headline),note(item.skills),d("actions",b("编辑",()=>candidateForm(item)),b("上传简历",()=>upload(item))),
    ])),pager(data.total)),search("搜索姓名或技能")));
}

function picker(key,title,path,caption,filter=()=>true) {
  const select=e("select",{name:key,required:true});
  const input=e("input",{placeholder:"查找"+title,"aria-label":"查找"+title});
  async function load() {
    select.replaceChildren(e("option",{value:""},"查询中…"));
    try {
      const results=await api(path+"?"+params({q:input.value.trim(),limit:50}));
      select.replaceChildren(e("option",{value:""},"请选择"+title),
        ...results.items.filter(filter).map(row=>e("option",{value:row.id},row[caption])));
    } catch(err) { select.replaceChildren(e("option",{value:""},"查询失败")); flash(err.message); }
  }
  input.addEventListener("keydown",event=>{if(event.key==="Enter"){event.preventDefault();load();}});
  load();
  return d("wide",d("toolbar",input,b("查找",load)),e("label",{},title,select));
}
function newApplication() {
  formModal("新增应聘记录",[
    picker("job_id","岗位","/jobs","title",row=>row.status==="open"),picker("candidate_id","候选人","/candidates","name"),
  ],(_,body)=>post("/applications",body));
}
async function assignment(app) {
  const options=await api("/interviewers");
  formModal("指派面试官",[choices("interviewer_id","面试官",[["","未指派"],...options.map(person=>[person.id,person.name])],app.interviewer_id)],
    (_,body)=>post("/applications/"+app.id+"/assign",{interviewer_id:body.interviewer_id||null,expected_version:app.version}));
}
async function applicationDetail(id) {
  const path="/applications/"+id;
  const [app,history,records]=await Promise.all([api(path),api(path+"/status-history"),api(path+"/feedback")]);
  const job=await api("/jobs/"+app.job_id);
  const hr=user.role==="hr", terminal=["rejected","hired","withdrawn"].includes(app.status);
  const allowed=hr?[...(stages[app.status]??[])]:app.status==="pending_interview"?["interviewing"]:[];
  if (hr&&!terminal&&job.status==="closed"&&app.status!=="reserve") {
    allowed.push(...["rejected","withdrawn","reserve"].filter(status=>!allowed.includes(status)));
  }
  const actions=allowed.filter(status=>status!=="interviewing"||app.interviewer_id).map(status=>
    b(label(status),()=>reason("更新为"+label(status),why=>post(path+"/transitions",{status,reason:why,expected_version:app.version})),"link"));
  if (hr&&!terminal) actions.unshift(b("分配面试官",()=>assignment(app)));
  if (hr&&terminal&&user.reopen_applications) actions.push(b("重新开启应聘",()=>reason("重新开启应聘",why=>
    post(path+"/reopen",{reason:why,expected_version:app.version}))));
  if (!hr&&app.status==="interviewing") actions.push(b("填写面试反馈",()=>formModal("人工面试反馈",[
    field("notes","面试记录","",{type:"textarea",required:true,wide:true,max:8000}),
  ],(_,body)=>post(path+"/feedback",{...body,expected_version:app.version}),"提交反馈"),"primary"));
  showModal(app.candidate_name+" · 应聘记录",d("dialog-details",detail([
    ["招聘岗位",job.title],["岗位状态",badge(job.status)],["当前流程",badge(app.status)],["面试官",app.interviewer_name],
  ]),d("actions",b("查看候选人与简历",()=>candidateDetail(app.candidate_id)),b("岗位证据与面试提纲",()=>showInterviewAI(id)),actions),
  e("h3",{},"状态历史"),table(["时间","操作者","状态","原因"],history.map(x=>[date(x.created_at),x.actor_name,badge(x.status),note(x.reason)])),
  e("h3",{},"面试反馈"),table(["时间","面试官","反馈"],records.map(x=>[date(x.created_at),x.actor_name,note(x.notes)]))));
}
function applicationsTable(items) {
  return table(["候选人","招聘岗位","状态","面试官","更新时间","操作"],items.map(item=>[
    item.candidate_name,item.job_title,badge(item.status),item.interviewer_name,date(item.updated_at),
    b("处理记录",()=>applicationDetail(item.id),"link"),
  ]));
}
async function applications() {
  const data=await api("/applications?"+params({offset,limit:pageSize}));
  return d("",heading(titles.applications,user.role==="hr"?b("新增应聘",newApplication,"primary"):null),
    panel("应聘记录",d("",applicationsTable(data.items),pager(data.total))));
}
async function home() {
  const [summary,recent]=await Promise.all([api("/dashboard"),api("/applications?limit=6")]);
  const stats=[["在招岗位",summary.open_jobs],["人才库候选人",summary.candidates],["待审核应聘",summary.pending_review]];
  return d("",heading(titles.home),e("section",{class:"panel summary","aria-label":"招聘指标"},
    stats.map(([title,value])=>d("",e("small",{},title),e("strong",{class:"metric"},String(value))))),
    panel("近期应聘",applicationsTable(recent.items),b("查看全部",()=>navigate("applications"),"link")),
    panel("流程与文件",d("panel-body actions","今日上传 "+summary.files_today+" 份 · 待面试 "+summary.pending_interview+" 条",
      Object.entries(summary.applications_by_status).map(([status,num])=>badge(label(status)+" "+num)))),
    e("small",{},"智能解析、人才语义检索及 AI 助手将在后续阶段开放。"));
}

function userForm(account) {
  const roles=["admin","hr","interviewer"].map(role=>[role,label(role)]);
  formModal(account?"修改账号授权":"创建账号",[
    account?e("p",{class:"wide"},account.username+" · "+account.name):field("username","账号","",{required:true,max:80}),
    account?null:field("name","姓名","",{required:true,max:100}),
    account?null:field("password","初始密码（至少 12 字符）","",{type:"password",required:true,max:128}),
    choices("role","角色",roles,account?.role??"interviewer"),
    account?check("active","账号启用",account.active):null,
    account?check("view_contacts","授权 HR 读取联系方式及敏感简历",account.view_contacts):null,
    account?check("reopen_applications","授权 HR 重开应聘终态",account.reopen_applications):null,
  ],(form,body)=>account?patch("/users/"+account.id,{role:body.role,active:form.has("active"),
    view_contacts:form.has("view_contacts"),reopen_applications:form.has("reopen_applications")}):post("/users",body));
}
async function users() {
  const rows=await api("/users");
  return d("",heading(titles.users,b("创建账号",()=>userForm(),"primary")),panel("用户列表",table(
    ["账号","姓名","角色","启用状态","权限","操作"],rows.map(account=>[
      account.username,account.name,badge(account.role),account.active?"启用":"停用",
      [account.view_contacts?"候选人敏感信息":"",account.reopen_applications?"重开应聘":""].filter(Boolean).join("、")||"—",
      account.id===user.id?"当前账号":b("调整授权",()=>userForm(account)),
    ]))));
}
async function audit() {
  const rows=await api("/audit-logs");
  return d("",heading(titles.audit),panel("最近操作",table(["时间","动作","对象","操作人 ID","说明"],rows.map(row=>[
    date(row.created_at),row.action,row.resource_type+" · "+row.resource_id,row.actor_id,note(row.summary),
  ]))));
}

const uploadStates = {
  queued:"等待解析", review_pending:"待人工审核", parse_failed:"解析失败", confirmed:"已确认",
  duplicate:"文件重复", rejected:"文件无效", running:"处理中", succeeded:"成功", failed:"失败",
};
const sections = [["employment","工作经历"],["project","项目经历"],["skill","技能"],["education","教育经历"],
  ["certificate","证书"],["preference","求职意向"],["summary","职业摘要"]];
const toEvidence = item => ({section:item.section,block_id:item.block_id,quote:item.quote});
const evidenceList = items => e("ul",{class:"evidence-list"},items.map(item=>
  e("li",{},e("small",{},item.source ?? item.location ?? ""),e("p",{},item.quote))));

function newBatch() {
  formModal("批量上传简历",[
    e("label",{class:"wide"},"选择文件",e("input",{name:"files",type:"file",multiple:true,required:true,accept:".pdf,.docx,.txt,.jpg,.jpeg,.png"})),
    check("synthetic","本批为合成验收材料"),
    check("model_authorized","本批已获授权，可交配置的模型服务处理职业信息"),
  ],async form=>{
    const files=form.getAll("files");
    if(files.length>50) throw Error("每批最多 50 份。");
    const batch=await post("/resume-batches",{synthetic:form.has("synthetic"),model_authorized:form.has("model_authorized")});
    const failed=[];
    for(const file of files) {
      try { await post("/resume-batches/"+batch.id+"/files?"+params({filename:file.name}),file); }
      catch(err) { failed.push(file.name+" · "+err.message); }
    }
    return ()=>showBatch(batch.id,failed);
  },"提交批次");
}
async function showBatch(id, failures=[]) {
  const batch=await api("/resume-batches/"+id);
  showModal("简历批次",d("dialog-details",failures.map(message=>d("form-error",message)),
    d("actions",b("刷新进度",()=>showBatch(id,failures))),
    table(["文件","审核状态","任务","操作"],batch.uploads.map(item=>[
      item.filename,uploadStates[item.status]??item.status,
      d("",item.tasks.map(task=>e("p",{},(task.kind==="index"?"索引":"解析")+" · "+(uploadStates[task.status]??task.status),
        task.error?" · "+task.error:"",task.status==="failed"&&(task.kind==="index"||item.status==="parse_failed")?
          b("重试",async()=>{await post("/tasks/"+task.id+"/retry");await showBatch(id);},"link"):null))),
      ["review_pending","parse_failed","confirmed"].includes(item.status)?b("查看与审核",()=>reviewResume(item.id,id),"link"):null,
    ]))));
}
async function resumePage() {
  const batches=await api("/resume-batches");
  return d("",heading(titles.resumes,b("批量上传",newBatch,"primary")),
    panel("最近上传批次",table(["提交时间","文件数","操作"],batches.map(batch=>
      [date(batch.created_at),String(batch.upload_count),b("查看进度",()=>showBatch(batch.id),"link")]))));
}
async function downloadOriginal(path, filename) {
  const res=await api(path,"GET",undefined,true);
  const url=URL.createObjectURL(await res.blob());
  e("a",{href:url,download:filename}).click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function reviewResume(id,batchId) {
  const path="/resume-uploads/"+id;
  const draft=await api(path+"/draft");
  const editing=["review_pending","parse_failed"].includes(draft.status);
  const evidence=draft.evidence.map(toEvidence);
  const output=d("");
  const redraw=()=>output.replaceChildren(...evidence.map((item,index)=>d("evidence-row",
    e("small",{},sections.find(([key])=>key===item.section)?.[1]+" · "+item.block_id),e("p",{},item.quote),
    editing?b("移除",()=>{evidence.splice(index,1);redraw();},"link"):null)));
  redraw();
  const anchor=choices("block","原文位置",draft.blocks.map(block=>[block.id,block.location]));
  const category=choices("section","职业信息分类",sections);
  const quotation=field("quote","原文引文","",{type:"textarea",max:750});
  const sources=d("source-blocks",draft.blocks.map(block=>d("source-block",e("small",{},block.location),
    e("pre",{},block.text),editing?b("选用此段",()=>{
      anchor.querySelector("select").value=block.id;
      quotation.querySelector("textarea").value=block.text.length<=750?block.text:"";
      quotation.querySelector("textarea").focus();
    },"link"):null)));
  const error=d("form-error");
  const details=e("form",{class:"fields"},draft.candidate_id?e("p",{class:"wide"},"已关联候选人"):[
    field("name","新候选人姓名","",{max:100}),field("headline","职业方向"),
    picker("candidate_id","或关联已有候选人","/candidates",item=>item.name),
    field("skills","人工补录技能与经历","",{type:"textarea",wide:true,max:8000}),
  ],field("note",draft.status==="parse_failed"?"人工处理原因（必填）":"审核备注","",{type:"textarea",wide:true,max:800}));
  let version=draft.version;
  async function save(confirm) {
    error.textContent="";
    try {
      if (!confirm) {
        const data=await patch(path+"/draft",{expected_version:version,evidence});
        version=data.version;
        flash("已保存审核草稿");
        return;
      }
      const data=Object.fromEntries(new FormData(details));
      const body={expected_version:version,evidence,note:data.note};
      if(!draft.candidate_id) {
        if(data.candidate_id) body.candidate_id=data.candidate_id;
        else body.candidate={name:data.name,headline:data.headline,skills:data.skills};
      }
      await post(path+"/confirm",body);
      flash("已确认入库");
      if(batchId) await showBatch(batchId); else await reviewResume(id);
    } catch(err) { error.textContent=err.message; }
  }
  showModal("审核 "+draft.filename,d("dialog-details",
    d("actions",batchId?b("返回批次",()=>showBatch(batchId)):null,
      b("下载原件",()=>downloadOriginal(path+"/original",draft.filename))),
    d("",uploadStates[draft.status]??draft.status,draft.error?" · "+draft.error:""),
    d("review-grid",d("",e("h3",{},"提取原文"),sources),d("",e("h3",{},"职业证据"),output,
      editing&&draft.blocks.length?d("fields",anchor,category,quotation,b("添加引文",()=>{
        const quote=quotation.querySelector("textarea").value.trim();
        if(!quote) return;
        evidence.push({section:category.querySelector("select").value,block_id:anchor.querySelector("select").value,quote});
        quotation.querySelector("textarea").value="";
        redraw();
      })):null)),
    editing?d("",details,error,d("actions",b("保存草稿",()=>save(false)),b("确认并入库",()=>save(true),"primary"))):null));
}

function queryPanel(placeholder, run, options=[]) {
  const output=d(""), button=submit("提交"), input=e("input",{name:"query",placeholder,"aria-label":placeholder,required:true,maxlength:800});
  const form=e("form",{class:"filters",onsubmit:async event=>{
    event.preventDefault();
    button.disabled=true;
    output.replaceChildren(d("empty","正在处理…"));
    try { await run(Object.fromEntries(new FormData(form)),output); }
    catch(err) { output.replaceChildren(d("form-error",err.message)); }
    finally { button.disabled=false; }
  }},input,options,button);
  return d("",form,output);
}
function searchResults(data) {
  return d("",e("p",{},"检索词　"+data.query+" · "+({
    hybrid_local:"本地混合检索",hybrid_semantic:"模型向量混合检索",hybrid_model_fallback:"模型不可用，已回退",
  }[data.mode]??data.mode)),data.items.length?data.items.map(item=>d("result-card",
    b(item.name,()=>candidateDetail(item.candidate_id),"link"),evidenceList(item.evidence))):d("empty","未找到已确认的职业证据"));
}
function talentPage() {
  return d("",heading(titles.talent),panel("搜索已审核简历",queryPanel("例如 美国站 Listing 优化",async(data,output)=>{
    const result=await post("/talent-search",{query:data.query,use_external_ai:data.external==="on"});
    output.replaceChildren(searchResults(result));
  },[check("external","使用已配置的模型理解查询")])));
}
function documentForm(baseId, item) {
  formModal(item?"上传新版本":"添加知识文档",[
    field("title","文档标题",item?.title,{required:true}),field("text","正文","",{type:"textarea",required:Boolean(item),wide:true,max:24000}),
    item?null:e("label",{class:"wide"},"或选择知识文档",e("input",{name:"file",type:"file",accept:".pdf,.docx,.txt,.jpg,.jpeg,.png"})),
    check("model_authorized","该版本已获授权，可用于外部模型问答"),
  ],async(form,body)=>{
    const value={title:body.title,text:body.text,model_authorized:form.has("model_authorized")};
    if(item) await api("/knowledge-documents/"+item.id,"PUT",{...value,expected_version:item.version});
    else if(body.file?.name) await post("/knowledge-bases/"+baseId+"/files?"+params({
      title:value.title,filename:body.file.name,model_authorized:value.model_authorized,
    }),body.file);
    else await post("/knowledge-bases/"+baseId+"/documents",value);
  });
}
function kbResults(items) {
  return items.length?items.map(item=>d("result-card",e("strong",{},item.title),evidenceList([item]))):d("empty","当前知识库没有匹配证据");
}
async function showKnowledge(base) {
  const path="/knowledge-bases/"+base.id;
  const documents=await api(path+"/documents");
  showModal(base.title,d("dialog-details",user.role==="hr"?b("添加知识文档",()=>documentForm(base.id),"primary"):null,
    table(["文档","版本","操作"],documents.map(item=>[item.title,String(item.version),user.role==="hr"?d("actions",
      b("上传新版本",()=>documentForm(base.id,item)),b("归档",()=>formModal("归档 "+item.title,
        [e("p",{class:"wide"},"归档后，此文档将退出检索。")],()=>api("/knowledge-documents/"+item.id,"DELETE",{expected_version:item.version}),"确认归档"))):"—"])),
    queryPanel("搜索词或招聘制度问题",async(data,output)=>{
      const result=await post(path+(data.model==="on"?"/answer":"/search"),{query:data.query});
      output.replaceChildren(...[kbResults(result.citations??result.items)].flat());
    },[check("model","请求模型按授权知识回答")])));
}
async function knowledgePage() {
  const bases=await api("/knowledge-bases");
  const add=user.role==="hr"?b("新建知识库",()=>formModal("新建知识库",[
    field("title","知识库名称","",{required:true}),check("interviewer_visible","面试官可查看"),
  ],(form,body)=>post("/knowledge-bases",{title:body.title,interviewer_visible:form.has("interviewer_visible")})),"primary"):null;
  return d("",heading(titles.knowledge,add),panel("招聘知识库",table(["名称","范围","操作"],bases.map(base=>
    [base.title,base.interviewer_visible?"HR 与面试官":"仅 HR",b("打开",()=>showKnowledge(base),"link")]))));
}
function showGuide(guide,appId) {
  const output=d("dialog-details",guide.questions.map((question,i)=>d("result-card",
    e("strong",{},(i+1)+". "+question.question),detail([["考察点",question.focus],["参考答案",question.reference_answer],["追问",question.followup]]))));
  showModal("面试提纲",output,d("dialog-footer",b("返回",()=>showInterviewAI(appId)),
    user.role==="hr"?b("编辑题目",()=>formModal("编辑面试题",guide.questions.flatMap((item,i)=>[
      field("question"+i,"问题 "+(i+1),item.question,{type:"textarea",wide:true,required:true,max:2200}),
      field("focus"+i,"考察点",item.focus,{max:2200,required:true}),
      field("reference_answer"+i,"参考答案",item.reference_answer,{type:"textarea",wide:true,required:true,max:2200}),
      field("followup"+i,"追问",item.followup,{type:"textarea",wide:true,required:true,max:2200}),
    ]),(_,body)=>patch("/interview-guides/"+guide.id,{expected_version:guide.version,
      questions:guide.questions.map((q,i)=>({...q,...Object.fromEntries(["question","focus","reference_answer","followup"].map(key=>[key,body[key+i]]))}))}))):null));
}
async function showInterviewAI(appId) {
  const path="/applications/"+appId;
  const hr=user.role==="hr";
  const [analyses,guides]=await Promise.all([hr?api(path+"/analyses"):[],api(path+"/interview-guides")]);
  showModal("岗位证据与面试提纲",d("dialog-details",
    hr?b("分析已确认简历",async()=>{await post(path+"/analyses",{});await showInterviewAI(appId);},"primary"):null,
    analyses.map(analysis=>d("result-card",e("h3",{},"岗位版本 "+analysis.job_version),analysis.findings.map(item=>d("",
      e("strong",{},item.requirement+" · "+({evidence_found:"找到证据",needs_verification:"待核实",not_found:"未找到"}[item.status])),
      evidenceList(item.evidence),e("p",{},item.followup))),
      analysis.reviewed_by?b("生成面试提纲",()=>formModal("生成面试提纲",[
        field("count","题数",4,{type:"number",required:true}),choices("difficulty","难度",[["基础","基础"],["中等","中等"],["进阶","进阶"]],"中等"),
      ],(_,body)=>post("/analyses/"+analysis.id+"/interview-guides",{count:Number(body.count),difficulty:body.difficulty}))):
        b("确认审核",()=>reason("审核证据",why=>post("/analyses/"+analysis.id+"/review",{note:why}))))),
    table(["题数","模型","操作"],guides.map(guide=>[String(guide.questions.length),guide.model,b("查看题目",()=>showGuide(guide,appId),"link")]))));
}
async function chatEvents(path, body, handle) {
  const response=await api(path,"POST",body,true);
  const reader=response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer="";
  try {
    for(;;) {
      const {value,done}=await reader.read();
      if(done) break;
      buffer+=value;
      let boundary;
      while((boundary=buffer.indexOf("\n\n"))!==-1) {
        const lines=buffer.slice(0,boundary).split("\n");
        buffer=buffer.slice(boundary+2);
        const kind=lines.find(line=>line.startsWith("event: "))?.slice(7);
        const payload=lines.find(line=>line.startsWith("data: "))?.slice(6);
        if(kind&&payload) handle(kind,JSON.parse(payload));
      }
    }
  } finally { reader.releaseLock(); }
}
function chatAnswer(result) {
  if(result.code) return d("form-error",result.message??result.code);
  const data=result.data;
  if(!data) return d("empty","处理中");
  if(data.items) return searchResults(data);
  if(data.citations) return d("",kbResults(data.citations));
  if(data.counts) return detail(Object.entries(data.counts).map(([key,count])=>[label(key),String(count)]));
  if(data.questions) return d("",data.questions.map(q=>d("result-card",e("strong",{},q.question),
    detail([["考察点",q.focus],["参考答案",q.reference_answer],["追问",q.followup]]))));
  return d("empty",data.status==="choose_scope"?"请选择要查询的岗位、应聘记录或知识库。":"请选择招聘范围内的问题。");
}
async function showChat(scope) {
  const path="/chat/sessions/"+scope.id;
  const [history,bases,jobsData,applicationsData]=await Promise.all([api(path+"/messages"),api("/knowledge-bases"),
    api("/jobs?limit=50"),api("/applications?limit=50")]);
  const pane=d("chat-history",history.map(turn=>d("result-card",e("strong",{},turn.query),chatAnswer(turn.result))));
  const fields=d("fields",choices("job_id","关联岗位",[["","不指定"],...jobsData.items.map(item=>[item.id,item.title])]),
    choices("knowledge_base_id","知识库",[["","不指定"],...bases.map(item=>[item.id,item.title])]),
    choices("application_id","应聘记录",[["","不指定"],...applicationsData.items.map(item=>[item.id,item.candidate_name+" · "+item.job_title])]));
  showModal(scope.title,d("dialog-details",b("刷新历史",()=>showChat(scope)),pane,
    queryPanel("输入招聘问题或继续追问",async(data,output)=>{
      const body={client_id:crypto.randomUUID(),query:data.query,...Object.fromEntries(
        ["job_id","knowledge_base_id","application_id"].map(key=>[key,data[key]||null]))};
      let complete=false;
      await chatEvents(path+"/stream",body,(kind,payload)=>{
        if(kind==="meta") output.replaceChildren(d("empty","模型正在处理 · "+payload.trace_id));
        if(kind==="result"||kind==="error") {complete=true;output.replaceChildren(e("strong",{},data.query),chatAnswer(payload));}
      });
      if(!complete) output.replaceChildren(d("form-error","响应中断。请刷新历史记录。"));
    },[fields])));
}
async function chatPage() {
  const sessions=await api("/chat/sessions");
  return d("",heading(titles.chat,b("新建会话",async()=>{const scope=await post("/chat/sessions",{});refresh();await showChat(scope);},"primary")),
    panel("会话",table(["主题","操作"],sessions.map(scope=>[scope.title,b("继续对话",()=>showChat(scope),"link")]))));
}

const render={home,jobs,candidates,applications,users,audit,resumes:resumePage,talent:talentPage,knowledge:knowledgePage,chat:chatPage};
if (token) {
  try { enter(await api("/auth/me")); } catch(err) { logoutUI(err.message); }
} else loginPage();
