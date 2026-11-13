// In-memory DOM only: no browser and no network.
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
class Element {
 constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.handlers = {}; this.dataset = {}; }
 append(...items) { this.children.push(...items); }
 replaceChildren(...items) { this.children = items; }
 setAttribute(k,v) { this.attrs[k] = v; }
 getAttribute(k) { return this.attrs[k]; }
 addEventListener(k,v) { this.handlers[k] = v; }
 focus() {}
 querySelectorAll(selector) {
  const parts = selector.split(' '), last = parts.pop().slice(1);
  const all = this.children.filter(c => c instanceof Element);
  return all.flatMap(c => [...((c.className || '').split(' ').includes(last) ? [c] : []), ...c.querySelectorAll('.' + last)]);
 }
 querySelector(selector) { return this.querySelectorAll(selector)[0]; }
}
const document = {createElement: tag => new Element(tag), createTextNode: text => ({textContent:text})};
const notices = [];
const context = vm.createContext({document, Node:Element, icon:()=>new Element('svg'), console, setTimeout:()=>{}});
vm.runInContext(fs.readFileSync('gui/js/ui.js','utf8').replace(/^import .*;\r?\n/gm,'').replace(/export /g,'') +
 '\nglobalThis.ui={el,card,pageHeader,button,pill,badge,kv,empty,note,formatTime,toast:()=>{}};', context);
vm.runInContext(fs.readFileSync('gui/js/pages/snapshots.js','utf8').replace(/^import .*;\r?\n/gm,'').replace(/export /g,'') +
 '\nglobalThis.probe={render,deleteSnapshot,restoreSelected,selection:()=>selected};', context);
const p = context.probe;
let posts = [], jobs = [], confirm = false, refreshed = 0, confirmations = 0;
const first = {name:'first',time:'2026-11-01T12:00:00',baseline:true,associated:true,sealed:true,files:3,delete_reason:'首次恢复点需保留'};
const old = {name:'old',time:'2026-11-13T12:00:00',kind:'manual',sealed:true,files:2,delete_reason:''};
const ctx = {state:{snapshots:[old,first],baseline:'first',plan:{snapshot:'first'}},busy:false,
 confirm:async()=>{confirmations++;return confirm;}, startJob:payload=>jobs.push(payload),navigate(){},
 api:{post:async(path,payload)=>{posts.push({path,payload});ctx.state.snapshots=[first];}},
 refresh:async()=>{refreshed++;}};
async function main() {
 let root=p.render(ctx);
 assert.equal(root.querySelectorAll('.snapshot-tile').length,2);
 assert.equal(p.selection(),'first');
 const tiles=root.querySelectorAll('.snapshot-tile');
 assert.equal(tiles[1].querySelector('.btn-snapshot-delete').disabled,true);
 tiles[0].querySelector('.snapshot-pick').handlers.click();
 assert.equal(p.selection(),'old');
 await p.deleteSnapshot(ctx,first); assert.equal(confirmations,0);
 await p.deleteSnapshot(ctx,old); assert.equal(posts.length,0,'cancel must not delete');
 confirm=true;
 await p.restoreSelected(ctx); assert.equal(jobs[0].snapshot,'old');
 await p.deleteSnapshot(ctx,old);
 assert.equal(posts[0].path,'/api/snapshot-delete'); assert.equal(posts[0].payload.confirm,'old');
 assert.equal(refreshed,1);
 root=p.render(ctx); assert.equal(p.selection(),'first');
 assert.equal(root.querySelectorAll('.snapshot-tile').length,1);
 ctx.busy=true; await p.deleteSnapshot(ctx,old); assert.equal(posts.length,1);
 console.log('Snapshot UI checks passed: cards, selection, protection, cancellation, restore and deletion refresh');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
