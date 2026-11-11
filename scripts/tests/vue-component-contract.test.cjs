'use strict'
// 使用本机已安装的官方编译器解析手写夹具，不安装依赖、不连接模型。
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { analyzeFiles, collectFiles, loadCompiler, LIMITS } = require('../../src/main/resources/vue/component-contract-check.cjs')
const compiler = loadCompiler(path.resolve(process.env.VUE_CHECK_COMPILER_ROOT || '.'))
const sfc = (script, template = '<div/>') => `<script setup>${script}</script><template>${template}</template>`
const child = sfc('defineProps({ filters: {type: Array, required: true}, active: {type: String, required: true} })')
function check(template, component = child, script = "import FilterBar from './FilterBar.vue'") {
  return analyzeFiles([{ path: 'src/App.vue', content: sfc(script, template) },
    { path: 'src/FilterBar.vue', content: component }], compiler)
}

test('缺失必填 props 的 v-model 不能代替 filters/active', () => {
  const r = check('<FilterBar v-model="filter"/>')
  assert.equal(r.status, 'failed')
  assert.deepEqual(r.issues.map(i => i.prop), ['filters', 'active'])
  assert.equal(r.checkedUsages, 1)
})
test('正确显式属性和事件接线通过', () => {
  assert.equal(check('<FilterBar :filters="filters" :active="active" @change="active=$event"/>').status, 'ok')
})
test('事件监听器可提供 onClick 属性，事件属性声明保守跳过', () => {
  for (const declaration of ['defineProps({onClick:{type:Function,required:true}})', "const handler=defineModel('onClick',{required:true})"]) {
    for (const template of ['<FilterBar @click="handle"/>', '<FilterBar v-on="listeners"/>', '<FilterBar/>']) {
      const r = check(template, sfc(declaration))
      assert.equal(r.status, 'ok')
      assert.equal(r.checkedUsages, 0)
      assert.equal(r.skippedUsages, 1)
    }
  }
})
test('kebab 组件名及属性名与 camelCase 对齐', () => {
  const component = sfc("defineProps({'currentValue': {type:String, required:true}})")
  assert.equal(check('<filter-bar :current-value="value"/>', component).status, 'ok')
  assert.equal(check('<filter-bar/>', component).status, 'failed')
})
test('默认与具名 defineModel 按实际 prop 匹配', () => {
  for (const [definition, binding] of [["defineModel({required:true})", 'v-model'], ["defineModel('text',{required:true})", 'v-model:text']]) {
    assert.equal(check(`<FilterBar ${binding}="value"/>`, sfc(`const value=${definition}`)).status, 'ok')
    assert.equal(check('<FilterBar/>', sfc(`const value=${definition}`)).status, 'failed')
  }
})
test('带默认值不强判必填缺失', () => {
  assert.equal(check('<FilterBar/>', sfc('defineProps({x:{required:true,default:1}})')).status, 'ok')
})
for (const template of ['<FilterBar v-bind="props"/>', '<FilterBar :[key]="value"/>',
  '<FilterBar v-model:[key]="value"/>', '<component :is="FilterBar"/>',
  '<FilterBar v-for="FilterBar in items"/>', '<div v-slot="FilterBar"><FilterBar/></div>', '<div v-pre><FilterBar/></div>']) {
  test(`不确定绑定跳过：${template}`, () => {
    const r = check(template)
    assert.equal(r.status, 'ok')
    assert.equal(r.checkedUsages, 0)
  })
}
test('类型 props、普通 script、展开声明和无法求值选项跳过', () => {
  const components = [
    '<script setup lang="ts">defineProps<{filters: string[]}>()</script><template><div/></template>',
    '<script>export default {props:{filters:{required:true}}}</script><template><div/></template>',
    sfc('defineProps({...other, filters:{required:true}})'),
    sfc('const {filters=[]}=defineProps({filters:{required:true}})'),
    sfc('defineProps(configuration)'),
  ]
  for (const component of components) {
    const r = check('<FilterBar/>', component)
    assert.equal(r.status, 'ok')
    assert.equal(r.checkedUsages, 0)
    assert.ok(r.skippedUsages > 0)
  }
})
test('重复选项与 computed/spread 不作错误的必填证明', () => {
  for (const declaration of ['{x:{required:true,required:false}}','{x:{required:true,...options}}', '{[key]:{required:true}}', '{x:{required:true},x:String}', "{'foo-bar':{required:true},fooBar:String}"]) {
    assert.equal(check('<FilterBar/>', sfc(`defineProps(${declaration})`)).status, 'ok')
  }
})
test('缺文件或截断不能作不存在 props 的证明', () => {
  const r = analyzeFiles([{path:'src/App.vue',content:sfc("import FilterBar from './FilterBar.vue'",'<FilterBar/>')},
    {path:'src/FilterBar.vue',content:child,truncated:true}],compiler)
  assert.equal(r.status,'ok'); assert.equal(r.checkedUsages,0); assert.equal(r.truncated,true)
})
test('未知 props 配置及 Vue 保留属性不作为硬缺失证据', () => {
  for (const declaration of ['{value:configuration}', '{$value:{required:true}}', '{key:{required:true}}', '{__proto__:{required:true}}']) {
    const r = check('<FilterBar/>', sfc(`defineProps(${declaration})`))
    assert.equal(r.status, 'ok'); assert.equal(r.checkedUsages, 0)
  }
})
test('defineModel 与 defineProps 同名合并不猜测覆盖结果', () => {
  const r = check('<FilterBar/>', sfc('defineProps({modelValue:{required:true}}); const value=defineModel({default:1})'))
  assert.equal(r.status,'ok');assert.equal(r.checkedUsages,0)
})
test('最多四条阻断证据，超长文件跳过', () => {
  const r = check('<FilterBar/><FilterBar/><FilterBar/>')
  assert.equal(r.issues.length, LIMITS.issues)
  assert.equal(check('<FilterBar/>', ' '.repeat(LIMITS.chars+1)).status,'ok')
})
test('已有两个手写正例不会因检查器被拒绝', () => {
  const fixture = JSON.parse(fs.readFileSync(path.join(__dirname,'../../ai-service/evaluation/vue-review-contract-v1.json'),'utf8'))
  for (const c of fixture.cases) assert.equal(analyzeFiles(c.files,compiler).status,'ok',c.id)
})
test('扫描不跟随符号链接，不扫描依赖和构建，超界标记范围', t => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'vue-contract-test-'))
  try {
    fs.mkdirSync(path.join(root,'src'));fs.mkdirSync(path.join(root,'node_modules'));fs.mkdirSync(path.join(root,'dist'))
    fs.writeFileSync(path.join(root,'src/App.vue'),sfc(''))
    fs.writeFileSync(path.join(root,'node_modules/Hidden.vue'),child);fs.writeFileSync(path.join(root,'dist/Hidden.vue'),child)
    try { fs.symlinkSync(path.join(root,'node_modules'),path.join(root,'linked'),'junction') }
    catch (e) { if(e.code!=='EPERM')throw e; t.diagnostic('当前平台未授权符号链接，保留其他扫描检查') }
    const input=collectFiles(root)
    assert.deepEqual(input.files.map(f=>f.path),['src/App.vue'])
    for(let i=0;i<25;i++)fs.writeFileSync(path.join(root,`src/File${i}.vue`),sfc(''))
    const limited=collectFiles(root);assert.equal(limited.files.length,24);assert.equal(limited.truncated,true)
  } finally {
    // 只删除本测试创建的系统临时子目录，真实工程从不参与。
    assert.equal(path.dirname(root),os.tmpdir());fs.rmSync(root,{recursive:true,force:true})
  }
})
