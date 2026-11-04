const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const elements = new Map();
const context = vm.createContext({
  document: {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, {addEventListener() {}});
      return elements.get(id);
    },
    addEventListener() {},
  },
  fetch: () => new Promise(() => {}),
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../../dashboard/static/decisions.js'), 'utf8'), context);
vm.runInContext(`
state.snap = {items: [{id:'example#7', repo:'example', number:7, title:'Synthetic',
  kind:'issue', age_days:1, local_status:'queued', url:'https://example.com/7', options:[],
  last_action:{option_label:'Implement', mode:'dispatch', dispatch_error:'<failed>'}}]};
state.filter = 'waiting';
`, context);
assert.equal(vm.runInContext('visibleItems().length', context), 1);
vm.runInContext("state.filter = 'done'", context);
assert.equal(vm.runInContext('visibleItems().length', context), 0);
const card = vm.runInContext('itemCard(state.snap.items[0])', context);
assert.match(card, /Queued, execution unconfirmed/);
assert.match(card, /Dispatch failed: &lt;failed&gt;/);
assert.match(card, /Open on GitHub/);
assert.doesNotMatch(card, /data-act="apply"|class="dec-card done"/);
assert.match(card, /Resolve the queued handoff/);
console.log('Queued card: waiting filter, not done, truthful status, escaped error, source link, no duplicate dispatch control. PASS');
