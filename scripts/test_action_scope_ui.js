// Offline action routing; no browser, network, or live system operations.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const source = fs.readFileSync('gui/js/pages/network.js', 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace(/export function /g, 'function ');
const ui = {
  card: (props, ...children) => ({ props, children }),
  el: (tag, props, ...children) => ({ tag, props, children }),
  note: (text) => ({ text }), helpTip: () => null,
  button: (label, options) => ({ label, ...options }),
};
const context = vm.createContext({ ui });
vm.runInContext(source + '\nglobalThis.actionsCard = actionsCard;', context);
const navigations = [];
const ctx = { state: { configured: true, plan: { node: 'Chosen' }, baseline: 'first',
  collection: { preview: { stale: false, refreshing: false } },
  preview: { current: { health: { recommendation: { reason: 'fresh recommendation' } } } } },
  busy: false, navigate: page => navigations.push(page) };
function flatten(node) { return node ? [node, ...(node.children || []).flatMap(flatten)] : []; }
let nodes = flatten(context.actionsCard(ctx));
assert.ok(nodes.some(node => node.text === 'fresh recommendation'));
nodes.find(node => node.label === '去规则更新').onClick();
nodes.find(node => node.label === '去一键配置').onClick();
assert.deepEqual(navigations, ['updates', 'overview']);
ctx.state.collection.preview.stale = true;
nodes = flatten(context.actionsCard(ctx));
assert.ok(!nodes.some(node => node.text === 'fresh recommendation'));
ctx.busy = true;
nodes = flatten(context.actionsCard(ctx));
assert.ok(nodes.filter(node => node.label).every(node => node.disabled));
console.log('Action scope UI checks passed');
