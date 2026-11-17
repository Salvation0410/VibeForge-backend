/** 固定读书清单搜索验收；只操作隔离页面，不读取Vue内部状态。 */
export function readingSearchInputs(document) {
  const inputs = [...document.querySelectorAll('input')].filter(e => !['hidden', 'checkbox', 'radio'].includes(e.type));
  const adds = [...document.querySelectorAll('button')].filter(e => e.textContent.replace(/\s/g, '') === '添加书籍');
  if (adds.length !== 1 || inputs.length !== 2) throw new Error('READING_SEARCH_FAILED:search-input-boundary');
  // 只认添加按钮所在最小单输入容器，拒绝两个输入混在同一容器的歧义结构。
  let addInput;
  for (let e = adds[0].parentElement; e && e !== document.body; e = e.parentElement) {
    const local = inputs.filter(input => e.contains(input));
    if (local.length === 1) { addInput = local[0]; break; }
    if (local.length > 1) break;
  }
  const searchInput = inputs.find(e => e !== addInput);
  if (!addInput || !searchInput || inputs.some(e => e.disabled || !e.isConnected)) throw new Error('READING_SEARCH_FAILED:search-input-boundary');
  return { addInput, searchInput };
}

export async function verifyReadingSearch(document, { tick, record = () => {} }) {
  const results = [], { addInput, searchInput } = readingSearchInputs(document);
  const original = ['小王子', '人类简史', '三体'], added = ['搜索验收乙', '搜索验收甲'];
  const label = e => e.textContent.replace(/\s/g, '');
  const buttons = name => [...document.querySelectorAll('button')].filter(e => label(e) === name);
  function check(value, name) {
    const result = { name: 'search-' + name, passed: Boolean(value) }; results.push(result); record(result);
    if (!result.passed) throw new Error('READING_SEARCH_FAILED:' + result.name);
  }
  function cards() {
    const found = [];
    for (const leaf of document.body.querySelectorAll('*')) {
      if (leaf.children.length || ![...original, ...added].some(t => [t, '《' + t + '》'].includes(leaf.textContent.trim()))) continue;
      let card;
      for (let e = leaf.parentElement, depth = 0; e && e !== document.body && depth < 8; e = e.parentElement, depth++) {
        const status = [...e.querySelectorAll('button')].filter(b => ['标记已读', '标记未读'].includes(label(b)));
        const remove = [...e.querySelectorAll('button')].filter(b => label(b) === '删除书籍');
        if (status.length === 1 && remove.length === 1) { card = { title: leaf.textContent.trim().replace(/^《|》$/g, ''), status: status[0], remove: remove[0] }; break; }
      }
      check(Boolean(card), 'card-boundary'); found.push(card);
    }
    return found;
  }
  function visible(expected, name) {
    check(JSON.stringify(cards().map(c => c.title)) === JSON.stringify(expected), name);
    check(buttons('删除书籍').length === expected.length, name + '-card-count');
  }
  function counts(total, read, name) {
    const m = document.body.innerText.match(/共\s*(\d+)\s*本\s*[·•・]?\s*已读\s*(\d+)\s*本/);
    check(m && +m[1] === total && +m[2] === read, name);
  }
  async function enter(input, value) {
    check(input.ownerDocument === document && input.isConnected && !input.disabled, 'connected-input');
    input.value = value; input.dispatchEvent(new document.defaultView.Event('input', { bubbles: true })); await tick();
  }
  async function click(name) {
    const found = buttons(name); check(found.length === 1 && !found[0].disabled, 'unique-control'); found[0].click(); await tick();
  }
  visible(original, 'initial'); counts(3, 0, 'initial-counts');
  await enter(searchInput, '  王  '); visible(['小王子'], 'trim-match'); counts(3, 0, 'search-global-counts');
  check(addInput.value === '', 'search-keeps-add-input');
  cards()[0].status.click(); await tick(); counts(3, 1, 'toggle-global-counts');
  await click('未读'); visible([], 'unread-intersection');
  await click('已读'); visible(['小王子'], 'read-intersection');
  await enter(searchInput, ''); visible(['小王子'], 'clear-keeps-filter');
  await click('全部'); await click('书名升序');
  await enter(searchInput, '三'); visible(['三体'], 'substring');
  for (const title of added) { await enter(addInput, title); await click('添加书籍'); }
  check(searchInput.value === '三', 'add-keeps-query'); counts(5, 1, 'add-global-counts');
  await enter(searchInput, '搜索验收');
  const sorted = [...added].sort((a, b) => a.localeCompare(b, 'zh-CN'));
  visible(sorted, 'search-keeps-sort');
  await click('已读'); visible([], 'added-read-intersection');
  await click('未读'); visible(sorted, 'added-unread-intersection');
  await click('添加顺序'); visible(added, 'restore-matching-insertion');
  cards()[0].remove.click(); await tick(); visible([added[1]], 'search-delete'); counts(4, 1, 'delete-global-counts');
  await enter(searchInput, '不存在的验收书名'); visible([], 'no-match'); counts(4, 1, 'empty-global-counts');
  await enter(searchInput, '   '); visible(['人类简史', '三体', added[1]], 'blank-restores-filter');
  await click('全部'); visible([...original, added[1]], 'restore-all-insertion'); counts(4, 1, 'final-global-counts');
  // 在搜索命中同名记录时，按卡片操作建立不同状态，再按状态确认只删去点击的记录。
  await enter(searchInput, '搜索验收甲');
  await enter(addInput, added[1]); await click('添加书籍'); visible([added[1], added[1]], 'matching-duplicates');
  cards()[1].status.click(); await tick(); counts(5, 2, 'duplicate-toggle-counts');
  check(cards().map(c => label(c.status)).join(',') === '标记已读,标记未读', 'duplicate-independent-state');
  await click('已读'); visible([added[1]], 'duplicate-read-intersection');
  cards()[0].remove.click(); await tick(); visible([], 'duplicate-read-delete'); counts(4, 1, 'duplicate-delete-counts');
  await click('全部'); visible([added[1]], 'duplicate-unread-preserved');
  check(label(cards()[0].status) === '标记已读', 'duplicate-survivor-state');
  await enter(searchInput, ''); visible([...original, added[1]], 'duplicate-clear-restores'); counts(4, 1, 'duplicate-final-counts');
  check(document.querySelectorAll('img').length === 0, 'no-images');
  return results;
}
