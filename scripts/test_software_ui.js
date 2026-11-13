// Offline software popover and package checks: no browser or network.
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const source = fs.readFileSync('gui/js/pages/software.js', 'utf8').replace(/^import .*;\r?\n/gm, '').replace(/export /g, '');
const buttons = [], links = [], notices = [];
const ui = new Proxy({}, {get: (_, name) => (...args) => {
 if (name === 'button') buttons.push({label:args[0], ...args[1]});
 if (name === 'el' && args[0] === 'a') links.push(args[1]);
 if (name === 'note') notices.push(args[0]);
 return {append(){},replaceWith(){}};
}});
const context = vm.createContext({ui, document:{createElement:()=>({append(){}})}});
vm.runInContext(source + '\nglobalThis.probe={render,releaseLink,packageCard,refreshControl};', context);
const p=context.probe;
assert.equal(p.releaseLink({status:'available',repository:'bohaibo/clash-claude-network',release_url:'https://example.invalid/release'}),null);
const ctx={state:{version:'2.2.0',software:{status:'error',reason:'发布源不可访问',checking:false}},busy:false,
 api:{post:async path=>path==='/api/package'?{current_version:'2.2.0',package:{download_url:'/api/packages/'+'a'.repeat(32),filename:'package.zip',size:1024,sha256:'fixture'}}:{checking:true}},
 acceptState:(key,value)=>{ctx.state[key]=value;},refresh:async()=>{}};
p.render(ctx);
assert(notices.includes('发布源不可访问'));
p.packageCard(ctx);
const exportButton=buttons.find(button=>button.label==='生成 ZIP 安装包');
exportButton.onClick().then(()=>{
 p.packageCard(ctx);
 assert(links.some(link=>link.download==='package.zip' && link.href==='/api/packages/'+'a'.repeat(32)));
 const button = {attrs:{}, classList:{toggle:(name,value)=>{button.updated=value;}}, setAttribute(k,v){this.attrs[k]=v;}};
 const hint = {};
 const panel = {dataset:{}, contains:()=>false, replaceChildren(){this.replaced=(this.replaced||0)+1;}};
 const nodes = {'software-update-button':button,'software-update-panel':panel,'software-update-label':hint};
 context.document.getElementById = id=>nodes[id];
 for (const status of ['available','current','no_release','unchecked','error']) {
  ctx.state.software = {status,latest_version:'2.3.0'};
  p.refreshControl(ctx);
  assert.equal(hint.hidden,status!=='available');
  assert.equal(button.updated,status==='available');
  assert.equal(button.hidden,status!=='available');
  if(status==='available') assert.match(button.attrs['aria-label'],/发现软件新版本 2.3.0/);
  if(status==='error') assert.match(button.attrs['aria-label'],/无法检查/);
 }
 ctx.state.software={status:'available',checking:true};
 p.refreshControl(ctx);
 assert.equal(button.hidden,true,'checking does not advertise a cached release');
 ctx.state.software.checking=false;ctx.softwareCheckStarted=false;
 p.refreshControl(ctx);
 assert.equal(button.hidden,true,'wait for a check initiated by this window');
 ctx.softwareCheckStarted=true;p.refreshControl(ctx);
 assert.equal(button.hidden,false);
 const redraws=panel.replaced;
 p.refreshControl(ctx);
 assert.equal(panel.replaced,redraws,'unchanged polling must preserve the open panel contents');
 console.log('Offline software UI checks passed: update icon states, stable panel, release links, errors and ZIP download');
}).catch(error=>{console.error(error);process.exitCode=1;});
