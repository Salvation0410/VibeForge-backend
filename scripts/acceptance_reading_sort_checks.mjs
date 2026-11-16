/** 固定读书清单的排序验收；只操作隔离页面，不读取框架内部状态。 */
export async function verifyReadingSort(document, { tick, record = () => {} }) {
  const results = [], original = ['小王子', '人类简史', '三体'];
  const added = ['Z排序验收', 'A排序验收'];
  const label = e => e.textContent.replace(/\s/g, '');
  const buttons = name => [...document.querySelectorAll('button')].filter(e => label(e) === name);
  function check(value, name) {
    const result = { name: 'sort-' + name, passed: Boolean(value) };
    results.push(result); record(result);
    if (!result.passed) throw new Error('READING_SORT_FAILED:' + result.name);
  }
  const titles = () => [...document.querySelectorAll('body *')].filter(e => e.children.length === 0)
    .map(e => e.textContent.trim().replace(/^《|》$/g, ''))
    .filter(t => [...original, ...added].includes(t));
  const matches = expected => JSON.stringify(titles()) === JSON.stringify(expected);
  async function click(name) {
    const found = buttons(name); check(found.length === 1 && !found[0].disabled, 'unique-control');
    found[0].click(); await tick();
  }
  // 不先点击添加顺序：否则无法发现默认排序已经改变的实现。
  await click('全部'); check(matches(original), 'default-insertion');
  for (const title of added) {
    const inputs = [...document.querySelectorAll('input')].filter(e => !['hidden', 'checkbox', 'radio'].includes(e.type));
    check(inputs.length === 1 && !inputs[0].disabled, 'unique-input');
    inputs[0].value = title; inputs[0].dispatchEvent(new document.defaultView.Event('input', { bubbles: true }));
    await tick(); await click('添加书籍');
  }
  const insertion = [...original, ...added];
  check(matches(insertion), 'added-insertion');
  await click('书名升序');
  const sorted = [...insertion].sort((a, b) => a.localeCompare(b, 'zh-CN'));
  check(matches(sorted), 'ascending');
  await click('未读'); check(matches(sorted), 'filter-keeps-sort');
  await click('全部'); await click('添加顺序');
  check(matches(insertion), 'restore-insertion');
  for (const title of added) {
    const leaves = [...document.querySelectorAll('body *')].filter(e => e.children.length === 0 && [title, '《' + title + '》'].includes(e.textContent.trim()));
    check(leaves.length === 1, 'unique-test-title');
    let remove;
    for (let e = leaves[0], depth = 0; e && e !== document.body && depth < 8; e = e.parentElement, depth++) {
      const found = [...e.querySelectorAll('button')].filter(b => label(b) === '删除书籍');
      if (found.length === 1) { remove = found[0]; break; }
    }
    check(remove && !remove.disabled, 'unique-remove'); remove.click(); await tick();
  }
  check(matches(original), 'restored-originals');
  const counts = document.body.innerText.match(/共\s*(\d+)\s*本\s*[·•・]?\s*已读\s*(\d+)\s*本/);
  check(counts && +counts[1] === 3 && +counts[2] === 0, 'restored-counts');
  // 留在升序，供调用方继续执行排序后的同名身份删除验收。
  await click('书名升序');
  check(matches([...original].sort((a, b) => a.localeCompare(b, 'zh-CN'))), 'final-ascending');
  return results;
}
