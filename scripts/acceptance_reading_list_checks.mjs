/**
 * “慢读书房”固定验收需求的浏览器交互探针，不适用于任意 Vue 应用。
 * 在调用方提供的隔离文档中添加一本书并切换状态；不生成应用、不调用模型。
 * 记录固定检查名和计数，不收集页面正文、源码、Cookie 或错误原文。
 */
export async function verifyReadingList(document, { tick, prefix = 'reading', record = () => {}, addInput = null }) {
  const results = []
  const text = () => document.body.innerText
  const label = element => element.textContent.replace(/\s/g, '')
  const buttons = name => [...document.querySelectorAll('button')].filter(b => label(b) === name)
  function check(value, name, counts) {
    const result = { name: `${prefix}-${name}`, passed: Boolean(value), ...(counts ? { counts } : {}) }
    results.push(result)
    record(result)
    if (!result.passed) throw new Error(`READING_ACCEPTANCE_FAILED:${result.name}`)
  }
  function button(name, step) {
    const found = buttons(name)
    // 不任取重复按钮，避免点中错误元素后仍产生貌似成功的结果。
    check(found.length === 1 && !found[0].disabled, step)
    return found[0]
  }
  function counts(total, read, step) {
    // 固定探针要求页面用“共 N 本 · 已读 M 本”展示计数；其他文案需另行适配。
    const match = text().match(/共\s*(\d+)\s*本\s*[·•・]?\s*已读\s*(\d+)\s*本/)
    const actual = match ? { total: Number(match[1]), read: Number(match[2]) } : null
    check(actual && actual.total === total && actual.read === read, step, actual)
  }
  async function until(predicate, step) {
    // 等待 Vue 渲染完成的实际条件；tick 由调用方提供，有界轮询不按固定机器速度判成功。
    for (let attempt = 0; attempt < 100; attempt++) {
      if (predicate()) return
      await tick()
    }
    check(false, step)
  }
  const unread = () => buttons('标记已读')
  const read = () => buttons('标记未读')
  const enter = (input, value) => {
    input.value = value
    input.dispatchEvent(new document.defaultView.Event('input', { bubbles: true }))
  }

  check(text().includes('慢读书房') && ['小王子', '人类简史', '三体'].every(t => text().includes(t)), 'initial-books')
  check(unread().length === 3 && read().length === 0, 'initial-status')
  counts(3, 0, 'initial-counts')
  // 新增搜索框时由调用方明确提供添加输入，不按页面顺序猜测。
  const inputs = addInput ? [addInput] : [...document.querySelectorAll('input')].filter(i => !['checkbox', 'radio', 'hidden'].includes(i.type))
  check(inputs.length === 1 && inputs[0]?.tagName === 'INPUT'
      && inputs[0].ownerDocument === document && inputs[0].isConnected
      && !['hidden', 'checkbox', 'radio'].includes(inputs[0].type) && !inputs[0].disabled, 'input')
  const input = inputs[0]
  const add = button('添加书籍', 'add-button')
  enter(input, '验收新书'); await tick(); add.click()
  await until(() => text().includes('验收新书') && unread().length === 4, 'add-timeout')
  check(text().includes('验收新书') && unread().length === 4, 'add')
  counts(4, 0, 'added-counts')
  enter(input, '   '); await tick(); add.click(); await tick()
  check(unread().length === 4 && read().length === 0, 'reject-empty')
  counts(4, 0, 'empty-counts')

  unread()[0].click()
  await until(() => read().length === 1 && unread().length === 3, 'toggle-timeout')
  counts(4, 1, 'toggled-counts')
  button('已读', 'read-filter-button').click(); await tick()
  check(read().length === 1 && unread().length === 0, 'read-filter')
  counts(4, 1, 'read-filter-counts')
  button('未读', 'unread-filter-button').click(); await tick()
  check(unread().length === 3 && read().length === 0, 'unread-filter')
  counts(4, 1, 'unread-filter-counts')
  button('全部', 'all-filter-button').click(); await tick()
  check(read().length === 1 && unread().length === 3, 'all-filter')
  counts(4, 1, 'all-filter-counts')
  read()[0].click(); await tick()
  check(read().length === 0 && unread().length === 4, 'toggle-back')
  counts(4, 0, 'restored-counts')
  check(document.querySelectorAll('img').length === 0, 'no-img-elements')
  return results
}
