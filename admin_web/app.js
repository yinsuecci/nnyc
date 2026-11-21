"use strict";
const $ = id => document.getElementById(id);
let token = "", busy = false, generation = 0;
const expandedNodes=new Set();
const names = {ready:"可分配",overloaded:"高负载，暂停分配",full:"容量已满",draining:"维护中",offline:"心跳已超时"};
const pct = (used,total) => total ? (100*used/total).toFixed(1)+"%" : "--";
const gib = n => (n/1024**3).toFixed(1)+" GiB";
function element(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function message(text){$("message").textContent=text;}
function workerTemplate(id,origin){
  const address=new URL(origin);
  if(address.protocol!=="https:"||address.username||address.password||!/^[0-9a-f]{32}$/.test(id))
    throw new Error("请通过可信 HTTPS 控制节点访问后台后再导出配置。");
  return {host:"127.0.0.1",port:9445,cert:"/run/nn-remote/fullchain.pem",
    key:"/run/nn-remote/privkey.pem",coordinator:address.origin,coordinator_ca:null,node_id:id,disk_path:"/"};
}
function downloadConfig(id){
  const config=workerTemplate(id,location.origin);
  const url=URL.createObjectURL(new Blob([JSON.stringify(config,null,2)+"\n"],{type:"application/json"}));
  const link=element("a");link.href=url;link.download="nn-relay-"+id+".json";
  document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function api(method,body){
  const response=await fetch("/admin-api/nodes",{method,headers:{Authorization:"Bearer "+token,"Content-Type":"application/json"},body:body?JSON.stringify(body):undefined,cache:"no-store",redirect:"error"});
  if(!response.ok)throw new Error(response.status===401?"管理员令牌无效或已过期":`请求失败 (${response.status})：${(await response.text()).slice(0,180)}`);
  return response.json();
}
function metric(label,value,detail){const e=element("div",undefined,"metric");e.append(element("span",label),element("strong",value),element("small",detail));return e;}
function nodeMetric(label,value,percent){const e=element("div");e.append(element("span",`${label} ${value}`));if(percent!==null){const bar=element("progress");bar.max=100;bar.value=percent;bar.setAttribute("aria-label",label);e.append(bar);}return e;}
function edit(node){$("node-id").value=node.id;$("name").value=node.name;$("url").value=node.url;$("name").disabled=$("url").disabled=true;for(const [id,key] of [["capacity","capacity"],["bandwidth","bandwidth_mbps"],["high","high"],["low","low"]])$(id).value=node[key];$("form-title").textContent="调整节点容量策略";$("save").textContent="保存策略";$("cancel-edit").hidden=false;window.location.hash="settings";syncSection();$("capacity").focus();}
function resetForm(){$("node-form").reset();$("node-id").value="";$("name").disabled=$("url").disabled=false;$("form-title").textContent="接入一台服务器";$("save").textContent="登记节点";$("cancel-edit").hidden=true;}
function drawTrend(samples){
  const chart=$("trend"), ns="http://www.w3.org/2000/svg";
  chart.replaceChildren();
  const end=samples.at(-1)?.time??0, start=end-3600;
  function shape(tag,attrs){const item=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))item.setAttribute(k,v);chart.append(item);return item;}
  for(const tick of [0,50,100]){
    const y=135-tick*1.2;
    shape("line",{x1:42,x2:597,y1:y,y2:y,stroke:"#e1e7ef"});
    shape("text",{x:1,y:y+4,fill:"#657589","font-size":11}).textContent=tick+"%";
  }
  let prev=null,count=0;
  for(const sample of samples){
    if(sample.time<start||!Number.isFinite(sample.cpu)){prev=null;continue;}
    const x=42+(sample.time-start)/3600*555,y=135-sample.cpu*1.2;
    if(prev&&sample.time-prev.time<=35)shape("line",{x1:prev.x,y1:prev.y,x2:x,y2:y,stroke:"#0759ed","stroke-width":2.5});
    else shape("circle",{cx:x,cy:y,r:2.5,fill:"#0759ed"});
    prev={x,y,time:sample.time};count++;
  }
  $("trend-note").textContent=count?"每 15 秒采样 · 仅保留本次服务运行数据，缺测留空":"等待在线节点采样";
}
function render(data){
  const t=data.totals;$("coverage").textContent=`${data.online} / ${data.total} 台在线 · ${data.sessions} 个信令会话 · ${data.notice}`;
  $("totals").replaceChildren(metric("在线节点",data.online+" / "+data.total,"按最近有效心跳"),metric("CPU 总占用",t.cpu_percent===null?"--":t.cpu_percent.toFixed(1)+"%",`${t.cpu_count} 个逻辑核心`),metric("内存占用",pct(t.memory_used,t.memory_total),`${gib(t.memory_used)} / ${gib(t.memory_total)}`),metric("信令会话",String(data.sessions),"含正在等待授权的请求"));
  drawTrend(data.history??[]);
  const alerts=$("alerts");alerts.replaceChildren();
  for(const n of data.nodes.filter(n=>n.status!=="ready"))alerts.append(element("p",n.name+" · "+(n.age===null?"等待首次心跳":names[n.status]),"alert-item"));
  if(!alerts.childElementCount)alerts.append(element("p",data.total?"当前节点均可分配":"尚未登记服务器","hint"));
  alerts.append(element("p",`集群磁盘 ${pct(t.disk_used,t.disk_total)} · 网络收发 ${(t.network_bps*8/1e6).toFixed(2)} Mbps`,"hint"));
  const c=data.coordinator;$("coordinator").textContent=c?`CPU ${c.cpu_percent.toFixed(1)}% · 内存 ${gib(c.memory_used)} / ${gib(c.memory_total)} · 磁盘 ${gib(c.disk_used)} / ${gib(c.disk_total)}`:"等待采样";
  const list=$("node-list");list.replaceChildren();if(!data.nodes.length)list.append(element("p","暂无节点。登记并启动中继服务器后，这里会显示真实占用。","hint"));
  if(data.nodes.length){const captions=element("div",undefined,"summary-row captions");for(const title of ["节点名称","状态","CPU","内存","会话"])captions.append(element("span",title));list.append(captions);}
  for(const n of data.nodes){
    const row=element("article",undefined,"node"), head=element("div",undefined,"node-header");
    const title=element("div"), pending=n.status==="offline" && n.age===null;
    title.append(element("h3",n.name),element("p",n.url));
    head.append(title,element("span",pending?"等待首次心跳":names[n.status],"badge "+(n.status==="ready"?"":n.status==="offline"?"offline":"warning")));
    row.append(head);
    const m=n.metrics, grid=element("div",undefined,"node-data");
    if(m){
      grid.append(nodeMetric("CPU",m.cpu_percent.toFixed(1)+"%",m.cpu_percent),
        nodeMetric("内存",pct(m.memory_used,m.memory_total),100*m.memory_used/m.memory_total),
        nodeMetric("磁盘",pct(m.disk_used,m.disk_total),100*m.disk_used/m.disk_total),
        nodeMetric("网络",(m.network_bps*8/1e6).toFixed(2)+" Mbps",null));
    }else{
      grid.append(element("p","本次控制服务启动后，尚未收到该节点的有效采样。","hint"));
    }
    row.append(grid,element("p",`已预留 ${n.reserved} / ${n.capacity} 个会话 · 停接 ${n.high}% / 恢复 ${n.low}%`));
    if(pending){
      row.append(element("p","接入检查：中继进程需配置此节点的 ID 和令牌，并能通过 HTTPS 访问控制节点。登记本身不会启动中继。","hint"));
    }else{
      row.append(element("p",`最近上报：${n.age} 秒前${n.status==="offline"?"。上方为历史采样，不计入集群总量。":""}`,"hint"));
    }
    const actions=element("div",undefined,"actions"),change=element("button","调整策略","secondary"),drain=element("button",n.drain?"恢复接入":"暂停新会话","secondary");
    change.onclick=()=>edit(n);
    drain.onclick=async()=>{drain.disabled=true;try{await api("POST",{id:n.id,drain:!n.drain});await refresh();}catch(e){message(e.message);}finally{drain.disabled=false;}};
    const config=element("button","下载节点配置模板","secondary");
    config.onclick=()=>{try{downloadConfig(n.id);}catch(e){message(e.message);}};
    actions.append(config,change,drain);row.append(actions);
    const detail=element("details",undefined,"server-record"),summary=element("summary",undefined,"summary-row");
    summary.append(element("strong",n.name),element("span",pending?"等待首次心跳":names[n.status],"badge "+(n.status==="ready"?"":n.status==="offline"?"offline":"warning")),
      element("span",m&&n.status!=="offline"?m.cpu_percent.toFixed(1)+"%":"—"),
      element("span",m&&n.status!=="offline"?pct(m.memory_used,m.memory_total):"—"),
      element("span",n.reserved+" / "+n.capacity));
    detail.open=expandedNodes.has(n.id);
    detail.ontoggle=()=>{if(detail.isConnected){if(detail.open)expandedNodes.add(n.id);else expandedNodes.delete(n.id);}};
    detail.append(summary,row);list.append(detail);
  }
}
async function refresh(){if(!token||busy)return;busy=true;const current=generation;try{const data=await api("GET");if(current!==generation)return;render(data);$("dashboard").hidden=false;$("login").hidden=true;$("updated").textContent="更新于 "+new Date().toLocaleTimeString();message("");}catch(e){if(current===generation){message(e.message);$("updated").textContent="更新失败，显示上次数据";}}finally{busy=false;}}
$("login-form").onsubmit=async event=>{event.preventDefault();token=$("token").value.trim();$("token").value="";generation++;await refresh();};
$("logout").onclick=()=>{token="";generation++;$("dashboard").hidden=true;$("login").hidden=false;$("credential-value").textContent="";$("credential").hidden=true;$("node-list").replaceChildren();$("updated").textContent="已退出";resetForm();message("");};
$("refresh").onclick=refresh;$("cancel-edit").onclick=resetForm;
$("dismiss-credential").onclick=()=>{$("credential-value").textContent="";$("credential").hidden=true;};
$("node-form").onsubmit=async event=>{event.preventDefault();const data={name:$("name").value.trim(),url:$("url").value.trim(),capacity:Number($("capacity").value),bandwidth_mbps:Number($("bandwidth").value),high:Number($("high").value),low:Number($("low").value)};if($("node-id").value)data.id=$("node-id").value;if(data.low>data.high){message("恢复水位不能高于停止分配水位");return;}$("save").disabled=true;const current=generation;try{const result=await api("POST",data);if(current!==generation)return;resetForm();await refresh();if(current!==generation)return;if(result.token){$("credential-value").textContent=`node_id: ${result.id}\nNN_NODE_TOKEN: ${result.token}`;$("credential").hidden=false;$("credential").scrollIntoView();}}catch(e){if(current===generation)message(e.message);}finally{$("save").disabled=false;}};
setInterval(()=>{if(!document.hidden)refresh();},5000);
$("import-form").onsubmit=async event=>{
  event.preventDefault();const current=generation, file=$("import-file").files[0];
  if(!file || file.size>4096){message("请选择不超过 4 KiB 的 JSON 清单");return;}
  $("import-submit").disabled=true;
  try{
    const rows=JSON.parse(await file.text());
    if(current!==generation)return;
    if(!Array.isArray(rows)||!rows.length||rows.length>32)throw new Error("清单必须是 1 至 32 个节点组成的数组");
    const allowed=new Set(["name","url","capacity","bandwidth_mbps","high","low"]);
    if(rows.some(row=>!row||typeof row!=="object"||Object.keys(row).some(k=>!allowed.has(k))))throw new Error("清单含不支持字段；请勿导入凭据");
    const result=await api("POST",{import:rows});
    if(current!==generation)return;
    $("credential-value").textContent=result.registered.map(n=>n.name+"\nnode_id: "+n.id+"\nNN_NODE_TOKEN: "+n.token).join("\n\n");
    $("credential").hidden=false;$("import-form").reset();$("credential").scrollIntoView();await refresh();
  }catch(e){if(current===generation)message(e.message);}finally{$("import-submit").disabled=false;}
};
function syncSection() {
  const links = Array.from(document.querySelectorAll("aside nav a"));
  const selected = links.find(link => link.hash === window.location.hash) || links[0];
  if (!selected) return;
  for (const panel of document.querySelectorAll("[data-view]")) {
    panel.hidden = "#" + panel.dataset.view !== selected.hash;
  }
  $("page-title").textContent = selected.textContent;
  for (const link of links) {
    if (link === selected) link.setAttribute("aria-current", "location");
    else link.removeAttribute("aria-current");
  }
}
window.addEventListener("hashchange", () => {
  syncSection();
  window.scrollTo(0, 0);
});
syncSection();
