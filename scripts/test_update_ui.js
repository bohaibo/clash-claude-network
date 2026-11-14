// Offline update-result presentation. No browser or network.
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const source = fs.readFileSync('gui/js/pages/updates.js', 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace(/export /g, '');
const badges = [], rows = [], notes = [];
const ui = new Proxy({}, {get: (_, name) => (...args) => {
  if (name === 'badge') badges.push(args);
  if (name === 'kv') rows.push(...args[0]);
  if (name === 'note') notes.push(args[0]);
  return {};
}});
const context = vm.createContext({ui});
vm.runInContext(source + '\nglobalThis.show = lastResultCard;', context);
for (const [last, expected] of [
  [{success:true, skipped:true}, '已跳过'],
  [{success:true, unchanged:true}, '已是最新'],
  [{success:true, migrated:true}, '已切换来源'],
  [{success:true, chain_profile_updated:true}, '成功'],
  [{success:true, unchanged:true, chain_traffic_updated:true}, '已是最新'],
  [{success:false, recovery_required:true}, '未完成'],
]) {
  context.show({}, {last:{time:'fixture', ...last, changes:{domains:{after:110189, added:2, removed:1}}}});
  assert.equal(badges.at(-1)[0], expected);
}
assert(rows.some(([label, text]) => label === '国内域名' && text.includes('110189')));
assert(notes.some(text => text.includes('恢复尚未完成')));
assert(rows.some(([label, text]) => label === '链式配置' && text.includes('已同步')));
assert(rows.some(([label, text]) => label === '链式流量' && text.includes('缓存用量')));
console.log('Offline update UI checks passed: skipped, unchanged, migrated, recovery and counts');
