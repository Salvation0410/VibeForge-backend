/** 固定读书清单的增量删除验收，仅在同源隔离页面运行；会删除所有测试书籍。 */
import { verifyReadingList } from './acceptance_reading_list_checks.mjs'

export async function verifyReadingDeletion(document, { tick, prefix = 'delete', record = () => {} }) {
  const results = []
  const save = result => { results.push(result); record(result) }
  await verifyReadingList(document, { tick, prefix: prefix + '-preserved', record: save })
  const text = () => document.body.innerText
  const label = e => e.textContent.replace(/\s/g, '')
  const buttons = name => [...document.querySelectorAll('button')].filter(b => label(b) === name)
  function check(value, name, counts) {
    const result = { name: prefix + '-' + name, passed: Boolean(value), ...(counts ? { counts } : {}) }
    save(result)
    if (!result.passed) throw new Error('READING_DELETE_FAILED:' + result.name)
  }
  function counts(total, read, step) {
    const match = text().match(/共\s*(\d+)\s*本\s*[·•・]?\s*已读\s*(\d+)\s*本/)
    const actual = match ? { total: Number(match[1]), read: Number(match[2]) } : null
    check(actual && actual.total === total && actual.read === read, step, actual)
  }
  function card(title) {
    // 从精确书名逐层向上，只接受一个状态按钮和一个删除按钮的最小容器。
    const titles = [...document.body.querySelectorAll('*')].filter(e => e.children.length === 0 && e.textContent.trim() === title)
    check(titles.length === 1, 'unique-title')
    for (let e = titles[0], depth = 0; e && e !== document.body && depth < 8; e = e.parentElement, depth++) {
      const found = [...e.querySelectorAll('button')]
      if (found.filter(b => ['标记已读', '标记未读'].includes(label(b))).length === 1
          && found.filter(b => label(b) === '删除书籍').length === 1) return found
    }
    check(false, 'unique-card-actions')
  }
  async function filter(name) {
    const found = buttons(name); check(found.length === 1 && !found[0].disabled, 'filter-button')
    found[0].click(); await tick()
  }
  async function act(title, action) {
    const button = card(title).find(b => label(b) === action)
    check(button && !button.disabled, 'card-action')
    button.click(); await tick()
  }
  async function remove(title, total, read, step) {
    await act(title, '删除书籍')
    for (let attempt = 0; attempt < 100 && text().includes(title); attempt++) await tick()
    check(!text().includes(title), step + '-removed')
    counts(total, read, step + '-counts')
  }
  await act('小王子', '标记已读'); counts(4, 1, 'before-delete')
  await filter('已读')
  check(buttons('标记未读').length === 1 && buttons('标记已读').length === 0, 'read-filter')
  await remove('小王子', 3, 0, 'read-delete')
  check(buttons('标记已读').length === 0 && buttons('标记未读').length === 0, 'read-filter-empty')
  await filter('未读')
  check(['人类简史', '三体', '验收新书'].every(t => text().includes(t)) && !text().includes('小王子'), 'global-after-read-delete')
  await remove('人类简史', 2, 0, 'unread-delete')
  await filter('全部')
  check(['三体', '验收新书'].every(t => text().includes(t)) && !text().includes('人类简史') && !text().includes('小王子'), 'global-after-unread-delete')
  await remove('三体', 1, 0, 'third-delete')
  await remove('验收新书', 0, 0, 'last-delete')
  check(buttons('删除书籍').length === 0 && buttons('标记已读').length === 0, 'empty-list')
  const inputs = [...document.querySelectorAll('input')].filter(i => !['checkbox', 'radio', 'hidden'].includes(i.type))
  check(inputs.length === 1 && buttons('添加书籍').length === 1, 'empty-add-controls')
  inputs[0].value = '删空后新书'; inputs[0].dispatchEvent(new document.defaultView.Event('input', { bubbles: true }))
  await tick(); buttons('添加书籍')[0].click(); await tick()
  check(text().includes('删空后新书'), 'readd'); counts(1, 0, 'readd-counts')
  await act('删空后新书', '标记已读'); counts(1, 1, 'readd-toggle-counts')
  await filter('未读'); check(buttons('删除书籍').length === 0, 'readd-unread-empty')
  await filter('已读'); check(text().includes('删空后新书') && buttons('标记未读').length === 1, 'readd-read-visible')
  return results
}
