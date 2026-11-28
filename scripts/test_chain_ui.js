// Offline interaction contract: no browser, network, or system mutations.
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('gui/js/pages/overview.js','utf8').replace(/^import .*;\r?\n/gm,'').replace(/export /g,'');
const warnings=[];
const context=vm.createContext({ui:{toast:message=>warnings.push(message),el:()=>({})},icon(){},console});
vm.runInContext(source+'\nglobalThis.probe={form,beginApply,applyRegionHint,chainReady,switchMode,loadChoices,testHost:()=>{choicesHost={replaceChildren(){}};renderChoices=()=>{};},resetDrafts:()=>{modeDrafts.single=null;modeDrafts.chain=null;},setData:d=>nodesState.data=d};',context);
const p=context.probe,jobs=[],confirmations=[];
const saved={name:'Saved chain',front_subscription:'front',front_node:'Entry HK',exit_subscription:'exit',exit_node:'Exit SG'};
p.setData({subscriptions:[{uid:'front',nodes:['Entry HK'],front_groups:['Automatic']},{uid:'exit',nodes:['Exit SG']},
 {uid:'saved',nodes:['Saved chain'],chain:saved}],chain_defaults:saved,
 regions:{exit:{'Exit SG':{code:'SG'}},front:{'Entry HK':{code:'HK'}}}});
Object.assign(p.form,{mode:'chain',node:'Exit SG',subscription:'exit',frontNode:'Entry HK',frontSubscription:'front',chainName:'My chain',acknowledged:true});
const ctx={state:{configured:true,capabilities:{managed_chain:true}},confirm:async v=>{confirmations.push(v);return true;},startJob:v=>jobs.push(v)};
(async()=>{
 p.applyRegionHint(ctx,p.form.node);assert.equal(p.form.region,'SG');assert(p.chainReady());
 await p.beginApply(ctx);assert.equal(jobs[0].node,'Exit SG');assert.equal(jobs[0].region,'SG');
 assert.equal(jobs[0].chain.name,'My chain');assert.equal(jobs[0].chain.front_node,'Entry HK');
 assert(confirmations[0].subtitle.includes('Entry HK → Exit SG'));
 Object.assign(p.form,{frontNode:'Automatic',frontGroup:true,acknowledged:true});
 assert(p.chainReady());
 await p.beginApply(ctx);
 assert.equal(jobs.pop().chain.front_group,true);
 assert(confirmations.pop().subtitle.includes('Automatic → Exit SG'));
 p.form.frontGroup=false;
 assert(!p.chainReady());
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
 p.resetDrafts();
 Object.assign(p.form,{mode:'single',node:'Same name',subscription:'original',region:'HK',regionAuto:{code:'HK'},regionManual:false});
 p.switchMode(ctx,'chain');
 ctx.loadNodes=async()=>({subscriptions:[{uid:'replacement',nodes:['Same name']}]});
 await p.loadChoices(ctx);
 p.switchMode(ctx,'single');
 assert.equal(p.form.node,'');
 assert.equal(p.form.region,'');
 p.setData({regions:{replacement:{'Same name':{code:'US'}}}});
 p.form.subscription='replacement';
 p.applyRegionHint(ctx,'Same name');assert.equal(p.form.region,'US');
 p.applyRegionHint(ctx,'Unknown');assert.equal(p.form.region,'');
 p.form.region='HK';p.form.regionManual=true;
 p.applyRegionHint(ctx,'Unknown');assert.equal(p.form.region,'HK');
 const staticExit={name:'Saved static',front_subscription:'front',front_node:'Entry HK',
   static_exit:{protocol:'socks5',ip:'93.184.215.1',port:1080}};
 p.setData({subscriptions:[{uid:'front',nodes:['Entry HK']},{uid:'static',nodes:[staticExit.name],chain:staticExit}]});
 ctx.state.capabilities.static_exit=true;
 p.switchMode(ctx,'static');
 Object.assign(p.form,{frontSubscription:'front',frontNode:'Entry HK',chainName:'Manual static',region:'US',
   staticProtocol:'socks5',staticIp:'93.184.215.1',staticPort:'1080',staticUser:'test',staticPassword:'',acknowledged:true});
 assert(!p.chainReady());
 p.form.staticPassword='test-secret';
 assert(p.chainReady());
 await p.beginApply(ctx);
 assert.equal(jobs.at(-1).node,'Entry HK');
 assert.equal(jobs.at(-1).subscription,'front');
 assert.equal(jobs.at(-1).chain.static_exit.port,1080);
 assert(!confirmations.at(-1).subtitle.includes('test-secret'));
 Object.assign(p.form,{chainName:staticExit.name,staticSaved:'static',acknowledged:true});
 await p.beginApply(ctx);
 assert.equal(jobs.at(-1).node,staticExit.name);
 assert.equal(jobs.at(-1).subscription,'static');
 assert(!('chain' in jobs.at(-1)));
 const singleStatic={name:'Saved direct',direct:true,static_exit:staticExit.static_exit};
 p.setData({subscriptions:[{uid:'direct',nodes:[singleStatic.name],chain:singleStatic}]});
 ctx.state.capabilities.static_direct=true;
 p.switchMode(ctx,'staticDirect');
 Object.assign(p.form,{chainName:'Manual direct',region:'SG',staticIp:'93.184.215.1',staticPort:'1080',acknowledged:true});
 assert(p.chainReady());
 await p.beginApply(ctx);
 assert.equal(jobs.at(-1).node,'Manual direct');
 assert.equal(jobs.at(-1).subscription,'');
 assert.equal(jobs.at(-1).chain.direct,true);
 assert(!('front_node' in jobs.at(-1).chain));
 assert(confirmations.at(-1).subtitle.includes('电脑 → 93.184.215.1 → 目标网站'));
 Object.assign(p.form,{chainName:singleStatic.name,staticSaved:'direct',acknowledged:true});
 await p.beginApply(ctx);
 assert.equal(jobs.at(-1).subscription,'direct');
 assert(!('chain' in jobs.at(-1)));

 // Secret Gist import mode contract checks
 const savedGist={name:'Saved Gist',gist_import:true,topology:'chain',front_node:'Entry HK',gist_hint:'octo/…123456',has_saved_gist:true};
 p.setData({subscriptions:[{uid:'ManagedChain111111111111',nodes:[savedGist.name],chain:savedGist}],regions:{}});
 ctx.state.capabilities.gist_import=true;
 const previewCalls=[];
 ctx.api={post:async(url,body)=>{
   previewCalls.push({url,body});
   return {
     token:'a'.repeat(32),
     replace_uid:body.saved_uid || '',
     summary:{
       default_name:'Gist SG Node',
       topology:'chain',
       topology_label:'链式代理配置',
       front_label:'Chain-Front-Pinned',
       front_count:1,
       front_group:false,
       exit_label:'Gist SG Node',
       udp_blocked:false,
       region_hint:{code:'SG',source:'gist'},
       gist_hint:'octo/…999999',
     },
   };
 }};
 vm.runInContext('Object.assign(globalThis.probe,{fetchGistPreview,gistImportReady});',context);
 p.switchMode(ctx,'gistImport');
 assert(!p.gistImportReady());
 p.form.gistUrl='https://gist.github.com/octo/'+'a'.repeat(32);
 await p.fetchGistPreview(ctx);
 assert.equal(previewCalls.length,1);
 assert.equal(previewCalls[0].url,'/api/gist-import/preview');
 assert.equal(p.form.gistToken,'a'.repeat(32));
 assert.equal(p.form.chainName,'Gist SG Node');
 assert.equal(p.form.region,'SG');
 assert.equal(p.form.gistRegionSource,'gist');
 assert(p.gistImportReady());
 p.switchMode(ctx,'single');
 p.switchMode(ctx,'gistImport');
 assert.equal(p.form.gistToken,'a'.repeat(32));
 assert.equal(p.form.chainName,'Gist SG Node');
 p.form.acknowledged=true;
 await p.beginApply(ctx);
 const importJob=jobs.at(-1);
 assert.equal(importJob.node,'Gist SG Node');
 assert.equal(importJob.subscription,'');
 assert.equal(importJob.region,'SG');
 assert.deepEqual(JSON.parse(JSON.stringify(importJob.gist_import)),{token:'a'.repeat(32),name:'Gist SG Node'});
 assert(!JSON.stringify(importJob).includes('gist.github.com'));
 Object.assign(p.form,{gistSaved:'ManagedChain111111111111',gistToken:'',gistSummary:null,gistReplaceUid:'',chainName:'Saved Gist',region:'JP',acknowledged:true});
 assert(p.gistImportReady());
 await p.beginApply(ctx);
 const reapplyJob=jobs.at(-1);
 assert.equal(reapplyJob.node,'Saved Gist');
 assert.equal(reapplyJob.subscription,'ManagedChain111111111111');
 assert(!('gist_import' in reapplyJob));
 await p.fetchGistPreview(ctx,{savedUid:'ManagedChain111111111111'});
 assert.equal(previewCalls.at(-1).body.saved_uid,'ManagedChain111111111111');
 assert.equal(p.form.gistReplaceUid,'ManagedChain111111111111');
 assert.equal(p.form.chainName,'Saved Gist');
 p.form.acknowledged=true;
 await p.beginApply(ctx);
 const refetchJob=jobs.at(-1);
 assert.equal(refetchJob.subscription,'ManagedChain111111111111');
 assert.deepEqual(JSON.parse(JSON.stringify(refetchJob.gist_import)),{token:'a'.repeat(32),name:'Saved Gist',replace_uid:'ManagedChain111111111111'});
 ctx.state.capabilities.gist_import=false;
 const beforeCount=jobs.length;
 await p.beginApply(ctx);
 assert.equal(jobs.length,beforeCount);
 console.log('Offline chain UI checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
