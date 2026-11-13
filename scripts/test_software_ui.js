// Offline software page checks: no browser or network.
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const source = fs.readFileSync('gui/js/pages/software.js', 'utf8').replace(/^import .*;\r?\n/gm, '').replace(/export /g, '');
const buttons = [], links = [], notices = [];
const ui = new Proxy({}, {get: (_, name) => (...args) => {
 if (name === 'button') buttons.push({label:args[0], ...args[1]});
 if (name === 'el' && args[0] === 'a') links.push(args[1]);
 if (name === 'note') notices.push(args[0]);
 return {append(){}};
}});
const context = vm.createContext({ui, document:{createElement:()=>({append(){}})}});
vm.runInContext(source + '\nglobalThis.probe={render,releaseLink};', context);
const p=context.probe;
assert.equal(p.releaseLink({status:'available',repository:'bohaibo/clash-claude-network',release_url:'https://example.invalid/release'}),null);
const ctx={state:{version:'2.2.0',software:{status:'error',reason:'发布源不可访问',checking:false}},busy:false,
 api:{post:async path=>path==='/api/package'?{current_version:'2.2.0',package:{download_url:'/api/packages/'+'a'.repeat(32),filename:'package.zip',size:1024,sha256:'fixture'}}:{checking:true}},
 acceptState:(key,value)=>{ctx.state[key]=value;},refresh:async()=>{}};
p.render(ctx);
assert(notices.includes('发布源不可访问'));
const exportButton=buttons.find(button=>button.label==='生成 ZIP 安装包');
exportButton.onClick().then(()=>{
 p.render(ctx);
 assert(links.some(link=>link.download==='package.zip' && link.href==='/api/packages/'+'a'.repeat(32)));
 console.log('Offline software UI checks passed: release link validation, error message and ZIP download');
}).catch(error=>{console.error(error);process.exitCode=1;});
