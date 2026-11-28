// Offline update-result presentation. No browser or network.
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const source = fs.readFileSync('gui/js/pages/updates.js', 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace(/export /g, '');
const badges = [], rows = [], notes = [], buttons = [], toggles = [];
const ui = new Proxy({}, {get: (_, name) => (...args) => {
  if (name === 'badge') badges.push(args);
  if (name === 'kv') rows.push(...args[0]);
  if (name === 'note') notes.push(args[0]);
  if (name === 'button') buttons.push({label:args[0], ...args[1]});
  if (name === 'el' && args[0] === 'input' && args[1].type === 'checkbox') toggles.push(args[1]);
  return {append(){},dataset:{}};
}});
const context = vm.createContext({ui,icon:()=>({}),document:{createElement:()=>({append(){}})}});
vm.runInContext(source + '\nglobalThis.show = lastResultCard;globalThis.page = render;globalThis.gistSettings = gistSyncSettings;', context);
for (const [last, expected] of [
  [{success:true, skipped:true}, '已跳过'],
  [{success:true, unchanged:true}, '已是最新'],
  [{success:true, migrated:true}, '已切换来源'],
  [{success:true, chain_profile_updated:true}, '成功'],
  [{success:true, unchanged:true, chain_traffic_updated:true}, '已是最新'],
  [{success:false, recovery_required:true}, '未完成'],
  [{success:true, recovery_only:true}, '已恢复'],
]) {
  context.show({}, {last:{time:'fixture', ...last, changes:{domains:{after:110189, added:2, removed:1}}}});
  assert.equal(badges.at(-1)[0], expected);
}
assert(rows.some(([label, text]) => label === '国内域名' && text.includes('110189')));
assert(notes.some(text => text.includes('恢复尚未完成')));
assert(rows.some(([label, text]) => label === '链式配置' && text.includes('已同步')));
assert(rows.some(([label, text]) => label === '链式流量' && text.includes('缓存用量')));
context.show({}, {last:{time:'fixture', success:true, mobile_gist_sync:{status:'failed', reason:'offline fixture'}}});
assert.equal(badges.at(-1)[0], '成功');
assert(rows.some(([label, text]) => label === 'Secret Gist 同步' && text.includes('offline fixture')));
const jobs=[];
const recoveryCtx={state:{configured:false,operation:{pending_rule_update:true}},busy:false,
 confirm:async()=>true,startJob:payload=>jobs.push(payload)};
context.page(recoveryCtx);
const recover=buttons.find(button=>button.label==='恢复上次更新');
assert(recover && !recover.disabled,'recovery remains available without a configured profile or prior result');
recover.onClick().then(async ()=>{
 assert.equal(jobs[0].action,'update-recover');assert.equal(jobs[0].confirm,'RECOVER-RULES');
 buttons.length=0;recoveryCtx.busy=true;context.page(recoveryCtx);
 assert(buttons.find(button=>button.label==='恢复上次更新').disabled,'busy operation blocks recovery');
 buttons.length=0;recoveryCtx.state.operation.pending_rule_update=false;context.page(recoveryCtx);
 assert(!buttons.some(button=>button.label==='恢复上次更新'),'resolved transaction removes recovery prompt');
 const gistPosts=[];
 const ctx={state:{update:{}}, busy:false,
  api:{post:async (path, payload)=>{gistPosts.push([path,payload.enabled]);return payload;}},
  acceptState:(key,value)=>{ctx.state[key]=value;}};
 context.gistSettings(ctx,ctx.state.update);
 const toggle=toggles.at(-1);
 assert.equal(toggle.checked,true);
 await toggle.onchange({target:{checked:false}});
 assert.deepEqual(gistPosts,[['/api/mobile-gist-auto-sync',false]]);
 context.gistSettings(ctx,ctx.state.update);
 assert.equal(toggles.at(-1).checked,false);
}).catch(error=>{console.error(error);process.exitCode=1;});
console.log('Offline update UI checks passed: skipped, unchanged, migrated, recovery and counts');
