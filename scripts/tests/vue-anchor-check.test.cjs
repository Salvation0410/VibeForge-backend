'use strict'
// 手写静态页面验证判定边界，不加载生成组件或调用模型。
const { test } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const fs = require('node:fs')
const os = require('node:os')
const { spawnSync } = require('node:child_process')
const { analyzeAnchors, loadCompiler, staticBootstrap, collectDocument } = require('../../src/main/resources/vue/component-contract-check.cjs')
const compiler = loadCompiler(path.resolve(process.env.VUE_CHECK_COMPILER_ROOT || '.'))
const sfc = (template, script = '') => `<script setup>${script}</script><template>${template}</template>`
const files = (template, script = '') => [{ path: 'src/App.vue', content: sfc(template, script) }]
const check = (input, partial = false, html = '<div id="app"></div>') => analyzeAnchors(input, compiler, partial, html)

test('静态锚点缺失报告有界源码位置，存在目标与命名锚点通过', () => {
  const r = check(files('<a href="#shop">购物</a>'))
  assert.equal(r.status, 'failed')
  assert.deepEqual(r.issues, [{ file: 'src/App.vue', target: 'shop' }])
  assert.equal(check(files('<a href="#shop"/><section id="shop"/>')).status, 'ok')
  assert.equal(check(files('<a href="#shop"/><a name="shop"/>')).status, 'ok')
  assert.equal(check(files('<a href="#app"/>')).status, 'ok')
})

test('跨静态本地组件查找目标，未挂载组件不提供目标', () => {
  const input = files('<a href="#shop"/><Products/>', "import Products from './Products.vue'")
  input.push({ path: 'src/Products.vue', content: sfc('<section id="shop"/>') })
  assert.equal(check(input).status, 'ok')
  input[0].content = sfc('<a href="#shop"/>')
  assert.equal(check(input).status, 'failed')
})

for (const template of ['<a href="#shop"/><div :id="id"/>', '<a href="#shop"/><div v-bind="attrs"/>',
  '<a href="#shop"/><div v-html="markup"/>', '<a href="#shop"/><router-view/>',
  '<a href="#shop"/><component :is="View"/>', '<a href="#shop"/><slot/>',
  '<a href="#shop"/><Unknown/>', '<a href="#shop"/><div v-custom/>']) {
  test(`动态页面不作不存在证明：${template}`, () => {
    const r = check(files(template))
    assert.equal(r.status, 'ok'); assert.equal(r.reason, 'DYNAMIC_PAGE'); assert.deepEqual(r.issues, [])
  })
}

test('动态链接、点击处理、外链和 top 不判缺失', () => {
  const r = check(files('<a :href="target"/><a href="#shop" @click.prevent="open"/><a href="/other#x"/><a href="#top"/><a href="#"/>' +
    '<a href="#shop" onclick="open()"/><a href="#shop" :onClick="open"/><a href="#shop" download/><a href="#shop" target="_blank"/>'))
  assert.equal(r.status, 'ok'); assert.equal(r.checkedLinks, 0)
})

test('DOM 动态创建、脚本式路由与普通 script 页面跳过', () => {
  for (const script of ["document.createElement('section')", "import {useRouter} from 'vue-router'", 'const node=h("div")',
    "import {onMounted as mounted} from 'vue';mounted(createTarget)",
    "import {createTarget} from './helpers';createTarget()", "import './plugin.js'", "const element={};element.setAttribute('id','shop')"]) {
    assert.equal(check(files('<a href="#shop"/>', script)).reason, 'DYNAMIC_PAGE')
  }
  assert.equal(check([{ path: 'src/App.vue', content: '<script>export default {}</script><template><a href="#x"/></template>' }]).reason, 'DYNAMIC_PAGE')
})

test('截断、超限、缺少根模板或 index 不作不存在证明', () => {
  assert.equal(check(files('<a href="#shop"/>'), true).reason, 'INCOMPLETE')
  assert.equal(check([{ path: 'src/App.vue', content: ' '.repeat(12001) }]).reason, 'INCOMPLETE')
  assert.equal(check(files('<a href="#shop"/>'), false, null).reason, 'INCOMPLETE')
  assert.equal(check([]).reason, 'NO_APP_TEMPLATE')
})

test('最多四条问题，忽略不支持的 URI 编码与非字面表达式', () => {
  const r = check(files(Array.from({ length: 10 }, (_, n) => `<a href="#x${n}"/>`).join('')))
  assert.equal(r.issues.length, 4)
  assert.equal(check(files('<a href="#%E4%B8%AD"/><a :href="\'#missing\'"/>')).status, 'ok')
})

test('重复 ID 不误判缺失，无法证明目标唯一或可见', () => {
  assert.equal(check(files('<a href="#shop"/><div id="shop"/><span id="shop"/>')).status, 'ok')
})

test('index 内联脚本、base 和附加外部脚本使页面跳过', () => {
  for (const html of ['<script>createTarget()</script>', '<base href="/other/"/>', '<script src="/plugin.js"/>']) {
    assert.equal(check(files('<a href="#shop"/>'), false, html).reason, 'DYNAMIC_PAGE')
  }
})

test('真实 Node CLI 读取隔离工程，缺失失败、接线通过、插件入口跳过', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vue-anchor-'))
  const modules = path.join(root, 'node_modules')
  try {
    fs.mkdirSync(path.join(root, 'src'))
    fs.writeFileSync(path.join(root, 'package.json'), '{}')
    fs.writeFileSync(path.join(root, 'index.html'), '<html><body><div id="app"></div><script type="module" src="/src/main.js"></script></body></html>')
    const main = "import {createApp} from 'vue';import App from './App.vue';createApp(App).mount('#app')"
    fs.writeFileSync(path.join(root, 'src/main.js'), main)
    fs.writeFileSync(path.join(root, 'src/App.vue'), sfc('<a href="#shop">购物</a>'))
    const compilerRoot = path.resolve(process.env.VUE_CHECK_COMPILER_ROOT || '.')
    fs.symlinkSync(path.join(compilerRoot, 'node_modules'), modules, 'junction')
    const run = () => {
      const child = spawnSync(process.execPath, [path.resolve('src/main/resources/vue/component-contract-check.cjs'), root, '--anchors'],
        { encoding: 'utf8', timeout: 15000, windowsHide: true })
      assert.equal(child.status, 0); assert.equal(child.stderr, '')
      return JSON.parse(child.stdout)
    }
    assert.equal(run().status, 'failed')
    fs.writeFileSync(path.join(root, 'src/App.vue'), sfc('<a href="#shop">购物</a><section id="shop"/>'))
    assert.equal(run().status, 'ok')
    fs.writeFileSync(path.join(root, 'src/main.js'), main.replace(".mount('#app')", ".use(plugin).mount('#app')"))
    assert.equal(staticBootstrap(root, compiler, collectDocument(root)), false)
    assert.equal(run().reason, 'DYNAMIC_PAGE')
  } finally {
    // 先移除依赖链接，再核对临时目录归属，避免清理外部工作区。
    if (fs.existsSync(modules)) fs.unlinkSync(modules)
    assert.equal(path.dirname(root), fs.realpathSync(os.tmpdir()))
    assert.ok(path.basename(root).startsWith('vue-anchor-'))
    fs.rmSync(root, { recursive: true, force: true })
  }
})
