'use strict'
// 手写事件契约夹具只经官方编译器解析，不执行组件和生成代码。
const { test } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { analyzeEvents, loadCompiler } = require('../../src/main/resources/vue/component-contract-check.cjs')
const compiler = loadCompiler(path.resolve(process.env.VUE_CHECK_COMPILER_ROOT || '.'))
const sfc = (script, template = '<button @click="emit(\'add-to-cart\')">加入</button>') =>
  `<script setup>${script}</script><template>${template}</template>`
const child = closed => sfc(`${closed ? 'defineOptions({inheritAttrs:false});' : ''}const emit=defineEmits(['add-to-cart'])`)
function check(template = '<Card @add="handle"/>', source = child(true), partial = false) {
  return analyzeEvents([{ path: 'src/App.vue', content: sfc("import Card from './Card.vue'", template) },
    { path: 'src/Card.vue', content: source }], compiler, partial)
}

test('关闭透传且显式事件声明时拒绝父级错接，正确事件通过', () => {
  const r = check()
  assert.equal(r.status, 'failed')
  assert.deepEqual(r.issues, [{ file: 'src/App.vue', component: 'Card', event: 'add' }])
  assert.equal(check('<Card @add-to-cart="handle"/>').status, 'ok')
})

test('默认透传组件的疑似错接只提示审查，不能判硬失败', () => {
  const r = check('<Card @add="handle"/>', child(false))
  assert.equal(r.status, 'ok')
  assert.equal(r.issues.length, 0)
  assert.equal(r.advisories.length, 1)
  assert.equal(r.advisories[0].event, 'add')
})

test('未监听的已声明事件合法，原生监听不作错接结论', () => {
  for (const t of ['<Card/>', '<Card @click="handle"/>', '<Card @keyup.enter="handle"/>', '<Card @pointerdown="handle"/>']) {
    const r = check(t)
    assert.equal(r.status, 'ok')
    assert.equal(r.advisories.length, 0)
  }
})

test('事件名称遵循 Vue 的 camelCase/kebab 兼容且不混淆不同名称', () => {
  const source = sfc("defineOptions({inheritAttrs:false});const emit=defineEmits(['addToCart'])", '<div/>')
  assert.equal(check('<Card @add-to-cart="handle"/>', source).status, 'ok')
  assert.equal(check('<Card @add="handle"/>', source).status, 'failed')
})

for (const template of ['<Card v-on="listeners"/>', '<Card @[event]="handle"/>', '<Card v-bind="attrs" @add="handle"/>',
  '<component :is="Card" @add="handle"/>', '<Card v-for="Card in items" @add="handle"/>',
  '<div v-slot="Card"><Card @add="handle"/></div>', '<Card v-model="value"/>']) {
  test(`动态监听和作用域保守跳过：${template}`, () => {
    const r = check(template)
    assert.equal(r.status, 'ok')
    assert.equal(r.advisories.length, 0)
  })
}

test('类型、展开、attrs 转发、动态 emit、emit 逃逸和模型声明均不强判', () => {
  for (const source of [
    '<script setup lang="ts">const emit=defineEmits<{add:[number]}>()</script><template><div/></template>',
    sfc('const emit=defineEmits([...events])'),
    sfc('const emit=defineEmits(["add-to-cart"]); const attrs=useAttrs()'),
    sfc('const emit=defineEmits(["add-to-cart"])', '<button v-bind="$attrs"/>'),
    sfc('const emit=defineEmits(["add-to-cart"]); emit(event)'),
    sfc('const emit=defineEmits(["add-to-cart"]); register(emit)'),
    sfc('const emit=defineEmits(["add-to-cart"]); const model=defineModel()'),
    sfc('const emit=defineEmits(["add-to-cart"]); emit("add")'),
    sfc('defineOptions({inheritAttrs:false});const props=defineProps({onAdd:Function});const emit=defineEmits(["add-to-cart"])', '<button @click="props.onAdd()"/>'),
    sfc('defineOptions({inheritAttrs:false});const emit=defineEmits(["add-to-cart"])', '<div>{{emit(event)}}</div>'),
  ]) {
    const r = check('<Card @add="handle"/>', source)
    assert.equal(r.status, 'ok')
    assert.equal(r.advisories.length, 0)
    assert.ok(r.skippedUsages > 0)
  }
})

test('命令行事件模式使用项目依赖且不混入 props 字段', t => {
  const fs = require('node:fs'), os = require('node:os')
  const { spawnSync } = require('node:child_process')
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vue-event-test-'))
  const modules = path.join(root, 'node_modules')
  try {
    fs.writeFileSync(path.join(root, 'package.json'), '{}')
    fs.writeFileSync(path.join(root, 'App.vue'), sfc("import Card from './Card.vue'", '<Card @add="h"/>'))
    fs.writeFileSync(path.join(root, 'Card.vue'), child(true))
    try { fs.symlinkSync(path.join(path.resolve(process.env.VUE_CHECK_COMPILER_ROOT || '.'), 'node_modules'), modules, 'junction') }
    catch (error) { if (error.code !== 'EPERM') throw error; t.skip('当前平台未授权依赖链接'); return }
    const r = spawnSync(process.execPath, [path.resolve(__dirname, '../../src/main/resources/vue/component-contract-check.cjs'), root, '--events'], { encoding: 'utf8' })
    assert.equal(r.status, 0)
    assert.equal(r.stderr, '')
    const result = JSON.parse(r.stdout)
    assert.equal(result.status, 'failed')
    assert.equal(result.checkedUsages, 1)
    assert.equal('skippedFiles' in result, false)
  } finally {
    // 先移除链接本身，再删除本测试的临时子目录，外部依赖从不参与递归清理。
    if (fs.existsSync(modules)) fs.unlinkSync(modules)
    assert.equal(path.dirname(root), os.tmpdir())
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('缺失/截断/超限源码不作错接证明，证据最多四条', () => {
  assert.equal(check(undefined, child(true), true).reason, 'INCOMPLETE')
  assert.equal(check(undefined, ' '.repeat(12001)).reason, 'INCOMPLETE')
  const r = check('<Card @a="h" @b="h" @c="h" @d="h" @e="h"/>')
  assert.equal(r.issues.length, 4)
})
