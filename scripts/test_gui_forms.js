// Run page logic against a tiny in-memory DOM. No browser or network access.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
class Element {
  constructor(...children) { this.dataset = {}; this.children = children.flat(Infinity).filter(x => x instanceof Element); }
  append(...nodes) { this.children.push(...nodes.filter(x => x instanceof Element)); }
  prepend(...nodes) { this.children.unshift(...nodes); }
  find(id) { if (this.id === id) return this; for (const child of this.children) { const found = child.find(id); if (found) return found; } return null; }
}
async function check(page, section) {
  let root, resolvePost, postCount = 0;
  const ui = new Proxy({}, { get: (_, name) => (...args) => {
    const node = new Element(...args);
    if (name === 'button') node.disabled = args[1]?.disabled;
    return node;
  }});
  const source = fs.readFileSync('gui/js/pages/' + page + '.js', 'utf8')
    .replace(/^import .*;\r?\n/gm, '').replace(/export /g, '');
  const context = vm.createContext({ ui, icon: () => new Element(),
    document: { createElement: () => new Element(), getElementById: id => root?.find(id) },
  });
  vm.runInContext(source + `
    globalThis.probe = { render, saveSettings, hasDraft,
      edit: values => { Object.assign(draft, values); draft.dirty = true; revision++; },
      draft: () => ({...draft}) };
  `, context);
  const p = context.probe;
  const ctx = { state: { configured: false, browser: { settings: { engine: 'auto', language: 'zh-SG' } },
    update: { mode: 'scheduled', time: '13:00', days: 1 } }, busy: false,
    api: { post: () => { postCount++; return new Promise(r => { resolvePost = r; }); } },
    acceptState: (key, value) => { ctx.state[key] = value; },
    refresh: async () => { root = p.render(ctx); },
  };
  root = p.render(ctx);
  const firstEdit = section === 'browser' ? {engine: 'edge', language: 'en-US'} : {mode: 'manual', time: '08:00', days: 3};
  const laterEdit = section === 'browser' ? {engine: 'chrome', language: 'ja-JP'} : {mode: 'scheduled', time: '10:00', days: 7};
  p.edit(firstEdit);
  const first = p.saveSettings(ctx);
  await p.saveSettings(ctx);
  assert.equal(postCount, 1, 'duplicate save is ignored');
  p.edit(laterEdit);
  const authoritative = section === 'browser' ? {settings: firstEdit} : firstEdit;
  resolvePost(authoritative);
  await first;
  assert.equal(p.hasDraft(), true, 'edits made during save must survive');
  for (const [key, value] of Object.entries(laterEdit)) assert.equal(p.draft()[key], value);
  const second = p.saveSettings(ctx);
  resolvePost(section === 'browser' ? {settings: laterEdit} : laterEdit);
  await second;
  assert.equal(p.hasDraft(), false, 'successful unchanged draft becomes clean');
  for (const [key, value] of Object.entries(laterEdit)) assert.equal(p.draft()[key], value);
}
Promise.all([check('updates', 'update')])
  .then(() => console.log('Offline form checks passed: duplicate save, in-flight edits, authoritative response'))
  .catch(error => { console.error(error); process.exitCode = 1; });
