// Offline interaction contract: no browser, network, or system mutations.
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('gui/js/pages/overview.js','utf8').replace(/^import .*;\r?\n/gm,'').replace(/export /g,'');
const warnings=[];
const context=vm.createContext({ui:{toast:message=>warnings.push(message),el:()=>({})},icon(){},console});
vm.runInContext(source+'\nglobalThis.probe={form,beginApply,applyRegionHint,chainReady,switchMode,loadChoices,testHost:()=>{choicesHost={replaceChildren(){}};renderChoices=()=>{};},resetDrafts:()=>{modeDrafts.single=null;modeDrafts.chain=null;},setData:d=>nodesState.data=d};',context);
const p=context.probe,jobs=[],confirmations=[];
const saved={name:'Saved chain',front_subscription:'front',front_node:'Entry HK',exit_subscription:'exit',exit_node:'Exit SG'};
p.setData({subscriptions:[{uid:'front',nodes:['Entry HK']},{uid:'exit',nodes:['Exit SG']},
 {uid:'saved',nodes:['Saved chain'],chain:saved}],chain_defaults:saved,
 regions:{exit:{'Exit SG':{code:'SG'}},front:{'Entry HK':{code:'HK'}}}});
Object.assign(p.form,{mode:'chain',node:'Exit SG',subscription:'exit',frontNode:'Entry HK',frontSubscription:'front',chainName:'My chain',acknowledged:true});
const ctx={state:{configured:true,capabilities:{managed_chain:true}},confirm:async v=>{confirmations.push(v);return true;},startJob:v=>jobs.push(v)};
(async()=>{
 p.applyRegionHint(ctx,p.form.node);assert.equal(p.form.region,'SG');assert(p.chainReady());
 await p.beginApply(ctx);assert.equal(jobs[0].node,'Exit SG');assert.equal(jobs[0].region,'SG');
 assert.equal(jobs[0].chain.name,'My chain');assert.equal(jobs[0].chain.front_node,'Entry HK');
 assert(confirmations[0].subtitle.includes('Entry HK → Exit SG'));
 p.form.frontNode='Missing';await p.beginApply(ctx);assert.equal(jobs.length,1);
 p.form.mode='single';await p.beginApply(ctx);assert.equal(jobs.length,2);assert(!('chain' in jobs[1]));
 p.resetDrafts();Object.assign(p.form,{mode:'single',node:'Entry HK',subscription:'front',region:'HK',regionManual:true,acknowledged:true,search:'my filter'});
 p.switchMode(ctx,'chain');assert.equal(p.form.node,'Exit SG');assert.equal(p.form.region,'SG');assert.equal(p.form.chainName,'Saved chain');
 assert.equal(p.form.acknowledged,false);
 p.form.chainName='Another chain';p.form.region='US';p.form.regionManual=true;
 p.switchMode(ctx,'single');assert.equal(p.form.node,'Entry HK');assert.equal(p.form.subscription,'front');
 assert.equal(p.form.region,'HK');assert.equal(p.form.search,'my filter');assert.equal(p.form.acknowledged,false);
 p.form.acknowledged=true;await p.beginApply(ctx);assert(!('chain' in jobs.at(-1)));assert.equal(jobs.at(-1).node,'Entry HK');
 p.switchMode(ctx,'chain');assert.equal(p.form.chainName,'Another chain');assert.equal(p.form.region,'US');assert.equal(p.form.frontNode,'Entry HK');
 assert.equal(p.form.node,'Exit SG');assert.equal(p.form.acknowledged,false);
 ctx.state.capabilities.managed_chain=false;p.form.acknowledged=true;const count=jobs.length;
 await p.beginApply(ctx);assert.equal(jobs.length,count);assert(warnings.some(w=>w.includes('重新打开')));
 p.testHost();Object.assign(p.form,{mode:'single',subscription:'',node:''});
 ctx.loadNodes=async()=>({subscriptions:[{uid:'exit',nodes:['Exit SG']},{uid:'saved',nodes:['Saved chain'],chain:saved}],plan_subscription:'saved',plan_node:'Saved chain',active_uid:'saved'});
 await p.loadChoices(ctx);assert.equal(p.form.subscription,'exit');assert.equal(p.form.node,'');
 p.form.node='Exit SG';await p.loadChoices(ctx);assert.equal(p.form.node,'Exit SG');
 console.log('Offline chain UI checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
