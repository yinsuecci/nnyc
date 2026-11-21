"use strict";
const test=require("node:test");
const assert=require("node:assert/strict");
const vm=require("node:vm");
const fs=require("node:fs");
const path=require("node:path");
const source=fs.readFileSync(path.join(__dirname,"../admin_web/app.js"),"utf8");
function setup(){
  const elements=new Map();
  function dom(){return {children:[],value:"",textContent:"",append(...c){this.children.push(...c);},
    replaceChildren(...c){this.children=c;},reset(){},scrollIntoView(){},setAttribute(){}};}
  const get=id=>{if(!elements.has(id))elements.set(id,dom());return elements.get(id);};
  const window={location:{hash:""},addEventListener(){}};
  const env=vm.createContext({URL,window,document:{getElementById:get,createElement:dom,querySelectorAll:()=>[]},setInterval(){}});
  vm.runInContext(source,env);
  return {env,get};
}
test("relay template matches worker example and does not export credentials",()=>{
  const {env}=setup(),id="a".repeat(32);
  const actual=JSON.parse(JSON.stringify(env.workerTemplate(id,"https://control.example:8443")));
  const expected=JSON.parse(fs.readFileSync(path.join(__dirname,"../relay-worker.example.json")));
  assert.deepEqual(actual,{...expected,node_id:id,coordinator:"https://control.example:8443"});
  assert.throws(()=>env.workerTemplate(id,"http://control.example"));
});
test("logout while a successful registration is refreshing never reveals its token",async()=>{
  const {env,get}=setup();
  let complete,started;
  const refreshing=new Promise(resolve=>{started=resolve;});
  env.api=async()=>({id:"a".repeat(32),token:"one-time-secret"});
  env.refresh=()=>{started();return new Promise(resolve=>{complete=resolve;});};
  const submission=get("node-form").onsubmit({preventDefault(){}});
  await refreshing;
  get("logout").onclick();
  complete();await submission;
  assert.equal(get("credential-value").textContent,"");
  assert.equal(get("credential").hidden,true);
});
