/** 同名书籍固定隔离验收；会修改页面内存，不适用于任意应用或用户使用中的页面。 */
export async function verifyReadingDuplicates(document, { tick, record = () => {}, addInput = null }) {
  const results = [], duplicate = '同名验收书', originals = ['小王子', '人类简史', '三体'];
  const label = e => e.textContent.replace(/\s/g, '');
  const buttons = name => [...document.querySelectorAll('button')].filter(e => label(e) === name);
  function check(value, name, counts) {
    const result = { name: 'duplicate-' + name, passed: Boolean(value), ...(counts ? { counts } : {}) };
    results.push(result); record(result);
    if (!result.passed) throw new Error('READING_DUPLICATE_FAILED:' + result.name);
  }
  async function until(predicate, name) {
    for (let attempt = 0; attempt < 100; attempt++) { if (predicate()) return; await tick(); }
    check(false, name);
  }
  const leaves = title => [...document.body.querySelectorAll('*')].filter(e =>
    e.children.length === 0 && [title, '《' + title + '》'].includes(e.textContent.trim()));
  function cards(title) {
    const found = [];
    for (const leaf of leaves(title)) {
      let card;
      // 只认最小卡片的显式状态与删除按钮，不用整页按钮数代替记录身份。
      for (let e = leaf, depth = 0; e && e !== document.body && depth < 8; e = e.parentElement, depth++) {
        const actions = [...e.querySelectorAll('button')];
        const status = actions.filter(b => ['标记已读', '标记未读'].includes(label(b)));
        const remove = actions.filter(b => label(b) === '删除书籍');
        if (status.length === 1 && remove.length === 1) { card = { element: e, status: status[0], remove: remove[0], read: label(status[0]) === '标记未读' }; break; }
      }
      check(card && !found.some(c => c.element === card.element), 'card-boundary');
      found.push(card);
    }
    return found;
  }
  function target(read) {
    const matches = cards(duplicate).filter(c => c.read === read);
    check(matches.length === 1, 'unique-state-target');
    return matches[0];
  }
  function counts(total, read, step) {
    const m = document.body.innerText.match(/共\s*(\d+)\s*本\s*[·•・]?\s*已读\s*(\d+)\s*本/);
    const actual = m ? { total: +m[1], read: +m[2] } : null;
    check(actual && actual.total === total && actual.read === read, step, actual);
  }
  async function filter(name) {
    const found = buttons(name); check(found.length === 1 && !found[0].disabled, 'filter-control');
    found[0].click(); await tick();
  }
  async function add() {
    // 新增搜索框时由调用方明确提供添加输入，不按页面顺序猜测。
    const inputs = addInput ? [addInput] : [...document.querySelectorAll('input')].filter(e => !['hidden', 'checkbox', 'radio'].includes(e.type));
    const adds = buttons('添加书籍');
    check(inputs.length === 1 && inputs[0]?.tagName === 'INPUT'
      && inputs[0].ownerDocument === document && inputs[0].isConnected
      && !['hidden', 'checkbox', 'radio'].includes(inputs[0].type) && !inputs[0].disabled && adds.length === 1, 'add-controls');
    inputs[0].value = duplicate; inputs[0].dispatchEvent(new document.defaultView.Event('input', { bubbles: true }));
    await tick(); check(!adds[0].disabled, 'add-enabled'); adds[0].click(); await tick();
  }
  async function act(read, action) {
    const card = target(read), button = card[action];
    check(button && button.isConnected && !button.disabled, 'target-action');
    button.click(); await tick();
  }
  function preserved(step) {
    check(originals.every(title => { const found = cards(title); return found.length === 1 && !found[0].read; }), step);
  }
  await filter('全部'); counts(3, 0, 'initial-counts'); preserved('initial-originals');
  check(cards(duplicate).length === 0, 'fresh-duplicate-title');
  await add(); await until(() => cards(duplicate).length === 1, 'first-add'); counts(4, 0, 'first-counts');
  await act(false, 'status'); counts(4, 1, 'first-read-counts');
  await add(); await until(() => cards(duplicate).length === 2, 'second-add'); counts(5, 1, 'pair-counts');
  target(true); target(false); preserved('pair-originals');
  await filter('已读'); check(cards(duplicate).length === 1, 'read-filter-one');
  await act(true, 'remove'); await until(() => cards(duplicate).length === 0, 'read-removed'); counts(4, 0, 'read-delete-counts');
  await filter('全部'); check(cards(duplicate).length === 1 && !target(false).read, 'unread-twin-preserved'); preserved('read-delete-originals');
  await act(false, 'status'); counts(4, 1, 'remaining-read-counts');
  await add(); await until(() => cards(duplicate).length === 2, 'third-add'); counts(5, 1, 'second-pair-counts');
  await filter('未读'); check(cards(duplicate).length === 1, 'unread-filter-one');
  await act(false, 'remove'); await until(() => cards(duplicate).length === 0, 'unread-removed'); counts(4, 1, 'unread-delete-counts');
  await filter('全部'); check(cards(duplicate).length === 1 && target(true).read, 'read-twin-preserved'); preserved('unread-delete-originals');
  await filter('已读'); await act(true, 'remove'); await until(() => cards(duplicate).length === 0, 'last-removed'); counts(3, 0, 'restored-counts');
  await filter('全部'); check(cards(duplicate).length === 0, 'no-duplicates-left'); preserved('restored-originals');
  return results;
}
