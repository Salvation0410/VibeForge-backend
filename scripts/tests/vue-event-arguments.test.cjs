'use strict'
// 只使用可信编译器解析安全夹具，绝不运行生成组件。
const { test } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { analyzeEvents, loadCompiler } = require('../../src/main/resources/vue/component-contract-check.cjs')
const compiler = loadCompiler(path.resolve(process.env.VUE_CHECK_COMPILER_ROOT || '.'))
const sfc = (script, template, lang = '') => `<script setup${lang}>${script}</script><template>${template}</template>`
const source = (second = "'new'") => sfc("const emit=defineEmits(['shop-now'])",
  `<button @click="emit('shop-now','featured')">选购</button><button @click="emit('shop-now',${second})">新品</button>`)
function check(fn = "function go(){document.getElementById('featured')?.scrollIntoView()}",
  child = source(), template = '<Hero @shop-now="go"/>', partial = false) {
  return analyzeEvents([
    { path: 'src/App.vue', content: sfc(`import Hero from './Hero.vue';${fn}`, template) },
    { path: 'src/Hero.vue', content: child },
  ], compiler, partial)
}

test('真实失败结构：两个导航值交给零形参固定目标函数，只给线索不硬阻断', () => {
  const result = check()
  assert.equal(result.version, 3)
  assert.equal(result.status, 'ok')
  assert.deepEqual(result.issues, [])
  assert.equal(result.argumentChecked, 1)
  assert.equal(result.argumentSkipped, 0)
  assert.deepEqual(result.argumentAdvisories, [{ kind: 'IGNORED_LITERAL_VARIANTS', file: 'src/App.vue', component: 'Hero', event: 'shop-now' }])
  assert.ok(!JSON.stringify(result).includes('featured'))
  assert.ok(!JSON.stringify(result).includes('scrollIntoView'))
})

test('有意忽略多个值也只能提示，不能臆测用户功能需求', () => {
  const r = check('function go(){ closeMenu() }')
  assert.equal(r.status, 'ok')
  assert.equal(r.argumentAdvisories.length, 1)
})

test('字面字符串与来源数严格有界，超限或未知来源整体跳过', () => {
  assert.equal(check(undefined, source("'" + 'x'.repeat(80) + "'")).argumentChecked, 1)
  assert.equal(check(undefined, source("'" + 'x'.repeat(81) + "'")).argumentChecked, 0)
  const many = (count, last = "'new'") => sfc("const emit=defineEmits(['shop-now'])",
    Array.from({length: count}, (_, i) => `<button @click="emit('shop-now',${i === count - 1 ? last : "'featured'"})"/>`).join(''))
  assert.equal(check(undefined, many(32)).argumentAdvisories.length, 1)
  for (const child of [many(33), many(33, 'unknown'), many(32, 'unknown')]) {
    const r = check(undefined, child)
    assert.equal(r.argumentChecked, 0)
    assert.deepEqual(r.argumentAdvisories, [])
  }
})

test('同值与单来源纳入覆盖但无变体线索', () => {
  for (const child of [source("'featured'"), sfc("const emit=defineEmits(['shop-now'])", '<button @click="emit(\'shop-now\',\'featured\')"/>')]) {
    const r = check(undefined, child)
    assert.equal(r.argumentChecked, 1)
    assert.deepEqual(r.argumentAdvisories, [])
  }
})

for (const fn of [
  'function go(id){document.getElementById(id)}', 'function go(...args){}', 'function go(id="featured"){}',
  'function go({id}){}', 'const go=()=>{}', 'async function go(){}', 'function* go(){}',
  'function go(){ use(arguments[0]) }', 'function go(){ use(this) }', 'function go(){eval("arguments[0]")}',
  'function go(){ function inner(){return 1}; inner() }', 'function go(){use(()=>arguments[0])}',
  'function go(){}; go=other', 'function go(){}; function wrap(go){return go}',
  'function go(){}; eval("go = value => consume(value)")',
  'function go(){}; function helper(){eval("go = value => consume(value)")}',
  'function go(){}; for (go of handlers) {}',
  'function go(){}; for ({run:go} of handlers) {}',
  'function go(){}; for (go in handlers) {}',
]) {
  test(`不支持或可能消费参数的处理函数保守跳过：${fn.slice(0, 65)}`, () => {
    const r = check(fn)
    assert.equal(r.argumentChecked, 0)
    assert.equal(r.argumentSkipped, 1)
    assert.deepEqual(r.argumentAdvisories, [])
  })
}

for (const value of ['target', '...args', '{id:1}', '[1]', '`new`', 'null', "'x',other", 'true', '12']) {
  test(`动态、复杂或非字符串载荷不分析：${value}`, () => {
    const r = check(undefined, source(value))
    assert.equal(r.argumentChecked, 0)
    assert.deepEqual(r.argumentAdvisories, [])
  })
}

for (const template of ['<Hero @shop-now="go($event)"/>', '<Hero @shop-now.stop="go"/>',
  '<Hero @[event]="go"/>', '<Hero v-bind="attrs" @shop-now="go"/>',
  '<Hero v-for="go in list" @shop-now="go"/>', '<div v-slot="go"><Hero @shop-now="go"/></div>']) {
  test(`动态监听或作用域不推断：${template}`, () => {
    const r = check(undefined, undefined, template)
    assert.equal(r.argumentChecked, 0)
    assert.deepEqual(r.argumentAdvisories, [])
  })
}

test('模板其他入口的动态求值或重绑定使参数分析跳过', () => {
  for (const expression of ["eval('replacement')", 'go=other', 'go++']) {
    const r = check(undefined, undefined, `<Hero @shop-now="go"/><button @click="${expression}"/>`)
    assert.equal(r.argumentChecked, 0)
    assert.deepEqual(r.argumentAdvisories, [])
  }
})

test('类型声明和不完整扫描不输出线索', () => {
  const r = analyzeEvents([
    {path:'src/App.vue', content:sfc("import Hero from './Hero.vue';function go(): void {}", '<Hero @shop-now="go"/>', ' lang="ts"')},
    {path:'src/Hero.vue', content:source()},
  ], compiler)
  assert.equal(r.argumentChecked, 0)
  const partial = check(undefined, undefined, undefined, true)
  assert.equal(partial.argumentChecked, 0)
  assert.equal(partial.argumentSkipped, 0)
  assert.deepEqual(partial.argumentAdvisories, [])
})

test('转发链不扩大到通用数据流，最多四条建议', () => {
  const forwarding = sfc("const emit=defineEmits(['shop-now']);function forward(v){emit('shop-now',v)}", '<button @click="forward(\'a\')"/>')
  assert.equal(check(undefined, forwarding).argumentChecked, 0)
  const r = check(undefined, undefined, '<div>' + '<Hero @shop-now="go"/>'.repeat(7) + '</div>')
  assert.equal(r.argumentChecked, 7)
  assert.equal(r.argumentAdvisories.length, 4)
})
