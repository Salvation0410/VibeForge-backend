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

function payloadFixture({ payload = '{id:props.product.id,size:selectedSize.value}', receiver = '{product,size}',
  forwards = 0, handler = 'handleAdd', extra = '', listener = 'handleAdd', partial = false } = {}) {
  const files = []
  let imported = 'Card'
  files.push({ path: 'src/Card.vue', content: sfc(`const props=defineProps({product:Object});const emit=defineEmits(['add']);function add(){emit('add',${payload})}`, '<button @click="add">加入</button>') })
  for (let i = 0; i < forwards; i++) {
    const component = `Section${i}`
    files.push({ path: `src/${component}.vue`, content: sfc(`import ${imported} from './${imported}.vue';const emit=defineEmits(['add']);function forward(payload){emit('add',payload)}`,
      `<${imported} @add="forward"/>`) })
    imported = component
  }
  files.push({ path: 'src/App.vue', content: sfc(`import ${imported} from './${imported}.vue';function ${handler}(${receiver}){cart.value.push(product.id)}${extra}`, `<${imported} @add="${listener}"/>`) })
  return analyzeEvents(files, compiler, partial)
}

test('字面载荷缺少接收解构键仅提示审查，不改变构建成功', () => {
  const r = payloadFixture()
  assert.equal(r.version, 3)
  assert.equal(r.status, 'ok')
  assert.equal(r.issues.length, 0)
  assert.deepEqual(r.payloadAdvisories, [{kind: 'MISSING_DESTRUCTURED_KEYS', file: 'src/App.vue', component: 'Card', event: 'add', missingKeys: ['product']}])
  assert.equal(r.payloadChecked, 1)
})

test('最多两跳原样转发保留字面键线索，第三跳保守跳过', () => {
  for (const forwards of [1, 2]) {
    const r = payloadFixture({forwards})
    assert.equal(r.status, 'ok')
    assert.deepEqual(r.payloadAdvisories[0].missingKeys, ['product'])
  }
  assert.equal(payloadFixture({forwards:3}).payloadAdvisories.length, 0)
})

test('真实商品事件改名转发仍能定位父级解构断点', () => {
  const files = [
    {path:'src/ProductCard.vue', content:sfc("const props=defineProps({product:Object});const emit=defineEmits(['add']);function add(){emit('add',{id:props.product.id,size:selectedSize.value})}", '<button @click="add"/>')},
    {path:'src/ProductSection.vue', content:sfc("import ProductCard from './ProductCard.vue';const emit=defineEmits(['add-to-cart']);function handleAdd(payload){emit('add-to-cart',payload)}", '<ProductCard @add="handleAdd"/>')},
    {path:'src/App.vue', content:sfc("import ProductSection from './ProductSection.vue';function handleAddToCart({product,size}){cart.value.push(product.id)}", '<ProductSection @add-to-cart="handleAddToCart"/>')},
  ]
  const r = analyzeEvents(files, compiler)
  assert.equal(r.status, 'ok')
  assert.deepEqual(r.payloadAdvisories, [{kind:'MISSING_DESTRUCTURED_KEYS',file:'src/App.vue',component:'ProductSection',event:'add-to-cart',missingKeys:['product']}])
})

test('匹配载荷、别名解构、动态及默认载荷均不产生缺键提示', () => {
  assert.equal(payloadFixture({payload:'{product:props.product,size:selectedSize.value}'}).payloadAdvisories.length, 0)
  assert.equal(payloadFixture({receiver:'{id: product,size}'}).payloadAdvisories.length, 0)
  assert.equal(payloadFixture({receiver:'{toString}'}).payloadAdvisories.length, 0)
  for (const payload of ['data', '{...data}', '{[key]:value}', 'buildPayload()', 'null']) {
    assert.equal(payloadFixture({payload}).payloadAdvisories.length, 0)
  }
  for (const receiver of ['{product = fallback,size}', '{product,size} = fallback', '{product,...rest}', '{product:{id},size}', 'payload']) {
    assert.equal(payloadFixture({receiver}).payloadAdvisories.length, 0)
  }
  for (const listener of ['handleAdd($event)', 'value => handleAdd(value)']) {
    assert.equal(payloadFixture({listener}).payloadAdvisories.length, 0)
  }
  assert.equal(payloadFixture({partial:true}).payloadAdvisories.length, 0)
  assert.equal(payloadFixture({extra:'handleAdd = other'}).payloadAdvisories.length, 0)
})

test('未知 emit 分支、转发变形和转发额外来源不能被已知载荷掩盖', () => {
  const files = [
    {path:'src/Card.vue',content:sfc("const emit=defineEmits(['add']);function add(){emit('add',{id:1})}function unknown(){emit('add',data)}", '<button @click="add"/>')},
    {path:'src/Section.vue',content:sfc("import Card from './Card.vue';const emit=defineEmits(['add']);function forward(payload){emit('add',payload)}", '<Card @add="forward"/>')},
    {path:'src/App.vue',content:sfc("import Section from './Section.vue';function handle({product}){}", '<Section @add="handle"/>')},
  ]
  assert.equal(analyzeEvents(files,compiler).payloadAdvisories.length,0)
  files[0].content = files[0].content.replace('function unknown(){emit(\'add\',data)}','')
  assert.equal(analyzeEvents(files,compiler).payloadAdvisories.length,1)
  const original=files[1].content
  for (const change of [original.replace("emit('add',payload)","payload.product=other;emit('add',payload)"),
    original.replace('</script>', 'forward(other)</script>'),
    original.replace('<Card @add="forward"/>','<Card @add="forward"/><button @click="forward"/>'),
    original.replace('<Card @add="forward"/>','<Card @add="forward"/>{{forward(other)}}')]) {
    files[1].content=change
    assert.equal(analyzeEvents(files,compiler).payloadAdvisories.length,0)
  }
})

test('顶层箭头函数、模板字面 emit 和有限缺键数量均被支持', () => {
  const files = [
    {path:'src/Card.vue', content:sfc("const emit=defineEmits(['add'])", '<button @click="emit(\'add\',{id:1})"/>')},
    {path:'src/App.vue', content:sfc("import Card from './Card.vue';const handle=({product,size,quantity,color,label})=>cart.value.push(product.id)", '<Card @add="handle"/>')},
  ]
  const r = analyzeEvents(files, compiler)
  assert.equal(r.payloadChecked, 1)
  assert.deepEqual(r.payloadAdvisories[0].missingKeys, ['product','size','quantity','color'])
  files[0].content = files[0].content.replace('<button', '{{emit(\'add\',unknown)}}<button')
  assert.equal(analyzeEvents(files,compiler).payloadAdvisories.length,0)
})

test('类型可选字段、嵌套 emit 作用域及非静态监听不能形成载荷结论', () => {
  const files = [
    {path:'src/Card.vue', content:sfc("const emit=defineEmits(['add']);function add(){emit('add',{id:1})}", '<button @click="add"/>')},
    {path:'src/App.vue', content:'<script setup lang="ts">import Card from "./Card.vue";function handle({product}:{product?:unknown}){}</script><template><Card @add="handle"/></template>'},
  ]
  assert.equal(analyzeEvents(files,compiler).payloadAdvisories.length,0)
  files[1].content=sfc("import Card from './Card.vue';function handle({product}){}", '<Card v-for="handle in list" @add="handle"/>')
  assert.equal(analyzeEvents(files,compiler).payloadAdvisories.length,0)
  files[1].content=sfc("import Card from './Card.vue';function handle({product}){}", '<Card @add="handle"/>')
  files[0].content=sfc("const emit=defineEmits(['add']);function add(){function nested(){emit('add',{id:1})}nested()}", '<button @click="add"/>')
  assert.equal(analyzeEvents(files,compiler).payloadAdvisories.length,0)
})

test('有限 v-for 的载荷计数独立于仍跳过作用域的名称硬门禁', () => {
  const files = [
    {path:'src/Card.vue', content:sfc("const emit=defineEmits(['add']);function add(){emit('add',{id:1})}", '<button @click="add"/>')},
    {path:'src/App.vue', content:sfc("import Card from './Card.vue';function handle({product}){}", '<Card v-for="product in products" @add="handle"/>')},
  ]
  for (const loop of ['product in products', 'product of products']) {
    files[1].content = sfc("import Card from './Card.vue';function handle({product}){}", `<Card v-for="${loop}" @add="handle"/>`)
    const r = analyzeEvents(files, compiler)
    assert.equal(r.checkedUsages, 0)
    assert.equal(r.skippedUsages, 1)
    assert.equal(r.payloadChecked, 1)
    assert.equal(r.payloadSkipped, 0)
    assert.equal(r.payloadAdvisories.length, 1)
  }
  for (const loop of ['handle in products', 'Card in products', 'card in products', '{id} in products',
    '(product,index) in products', 'product in getProducts()', 'product in state.products']) {
    files[1].content=sfc("import Card from './Card.vue';function handle({product}){}", `<Card v-for="${loop}" @add="handle"/>`)
    const r=analyzeEvents(files,compiler)
    assert.equal(r.payloadChecked,0,loop)
    assert.equal(r.payloadSkipped,1,loop)
    assert.equal(r.payloadAdvisories.length,0,loop)
    assert.equal(r.checkedUsages,0,loop)
  }
  files[1].content=sfc("import Card from './Card.vue';function handle({product}){}", '<div v-slot="product"><Card v-for="product in products" @add="handle"/></div>')
  assert.equal(analyzeEvents(files,compiler).payloadAdvisories.length,0)
})

// 原失败项目的完整三文件快照固定在夹具中，测试不依赖临时产物，也不执行源码。
test('真实完整源码的商品列表 v-for 保留载荷审查线索', () => {
  const files = [
  {
    "path": "src/App.vue",
    "content": "<script setup>\nimport { computed, ref } from 'vue'\nimport SiteHeader from './components/SiteHeader.vue'\nimport HeroSection from './components/HeroSection.vue'\nimport ProductSection from './components/ProductSection.vue'\nimport BrandStory from './components/BrandStory.vue'\nimport SiteFooter from './components/SiteFooter.vue'\nimport CartDrawer from './components/CartDrawer.vue'\n\nconst products = [\n  { id: 'shirt', name: '青屿白衬衫', price: 199, tone: '#e8e4da', note: '挺括棉感 · 通勤日常' },\n  { id: 'knit', name: '海风针织衫', price: 299, tone: '#cfdcd6', note: '柔软细针 · 微凉海风' },\n  { id: 'pants', name: '山岚长裤', price: 399, tone: '#c9c6bd', note: '垂顺廓形 · 山间雾色' }\n]\n\nconst cartItems = ref([])\nconst cartOpen = ref(false)\n\nconst totalCount = computed(() =>\n  cartItems.value.reduce((sum, item) => sum + item.quantity, 0)\n)\n\nconst totalPrice = computed(() =>\n  cartItems.value.reduce((sum, item) => sum + item.price * item.quantity, 0)\n)\n\nfunction scrollToSection(id) {\n  const target = document.getElementById(id)\n  if (target) {\n    target.scrollIntoView({ behavior: 'smooth', block: 'start' })\n  }\n}\n\nfunction handleAddToCart({ product, size }) {\n  const existing = cartItems.value.find(\n    (item) => item.productId === product.id && item.size === size\n  )\n  if (existing) {\n    existing.quantity += 1\n  } else {\n    cartItems.value.push({\n      key: `${product.id}-${size}`,\n      productId: product.id,\n      name: product.name,\n      price: product.price,\n      tone: product.tone,\n      size,\n      quantity: 1\n    })\n  }\n  cartOpen.value = true\n}\n\nfunction handleIncrease(key) {\n  const item = cartItems.value.find((entry) => entry.key === key)\n  if (item) {\n    item.quantity += 1\n  }\n}\n\nfunction handleDecrease(key) {\n  const item = cartItems.value.find((entry) => entry.key === key)\n  if (item && item.quantity > 1) {\n    item.quantity -= 1\n  }\n}\n\nfunction handleRemove(key) {\n  cartItems.value = cartItems.value.filter((entry) => entry.key !== key)\n}\n</script>\n\n<template>\n  <div class=\"page\">\n    <SiteHeader :cart-count=\"totalCount\" @navigate=\"scrollToSection\" @open-cart=\"cartOpen = true\" />\n\n    <main>\n      <HeroSection @shop=\"scrollToSection('products')\" />\n      <ProductSection :products=\"products\" @add-to-cart=\"handleAddToCart\" />\n      <BrandStory @view-products=\"scrollToSection('products')\" />\n    </main>\n\n    <SiteFooter @navigate=\"scrollToSection\" />\n\n    <CartDrawer\n      :open=\"cartOpen\"\n      :items=\"cartItems\"\n      :total-count=\"totalCount\"\n      :total-price=\"totalPrice\"\n      @close=\"cartOpen = false\"\n      @increase=\"handleIncrease\"\n      @decrease=\"handleDecrease\"\n      @remove=\"handleRemove\"\n    />\n  </div>\n</template>\n\n<style scoped>\n.page {\n  min-height: 100vh;\n  display: flex;\n  flex-direction: column;\n  overflow-x: hidden;\n}\n\nmain {\n  flex: 1;\n}\n</style>\n"
  },
  {
    "path": "src/components/ProductSection.vue",
    "content": "<script setup>\nimport ProductCard from './ProductCard.vue'\n\ndefineProps({\n  products: { type: Array, required: true }\n})\n\nconst emit = defineEmits(['add-to-cart'])\n\nfunction handleAdd(payload) {\n  emit('add-to-cart', payload)\n}\n</script>\n\n<template>\n  <section id=\"products\" class=\"section products-section\" aria-labelledby=\"products-title\">\n    <div class=\"container\">\n      <header class=\"section-head\">\n        <p class=\"eyebrow\">精选单品</p>\n        <h2 id=\"products-title\" class=\"section-title\">当季商品</h2>\n        <p class=\"section-sub\">三件基础单品，覆盖日常通勤与周末出行。</p>\n      </header>\n\n      <div class=\"product-grid\">\n        <ProductCard\n          v-for=\"product in products\"\n          :key=\"product.id\"\n          :product=\"product\"\n          @add=\"handleAdd\"\n        />\n      </div>\n    </div>\n  </section>\n</template>\n\n<style scoped>\n.products-section {\n  background: #faf8f4;\n}\n\n.product-grid {\n  display: grid;\n  grid-template-columns: repeat(3, minmax(0, 1fr));\n  gap: 24px;\n}\n\n@media (max-width: 860px) {\n  .product-grid {\n    grid-template-columns: repeat(2, minmax(0, 1fr));\n  }\n}\n\n@media (max-width: 560px) {\n  .product-grid {\n    grid-template-columns: minmax(0, 1fr);\n    gap: 18px;\n  }\n}\n</style>\n"
  },
  {
    "path": "src/components/ProductCard.vue",
    "content": "<script setup>\nimport { ref } from 'vue'\n\nconst props = defineProps({\n  product: { type: Object, required: true }\n})\n\nconst emit = defineEmits(['add'])\n\nconst sizes = ['S', 'M', 'L']\nconst selectedSize = ref('M')\n\nfunction selectSize(size) {\n  selectedSize.value = size\n}\n\nfunction handleAdd() {\n  emit('add', { id: props.product.id, size: selectedSize.value })\n}\n</script>\n\n<template>\n  <article class=\"product-card\">\n    <div class=\"product-visual\" :style=\"{ background: product.tone }\" aria-hidden=\"true\">\n      <span class=\"product-visual-mark\">{{ product.name }}</span>\n    </div>\n    <div class=\"product-body\">\n      <h3 class=\"product-name\">{{ product.name }}</h3>\n      <p class=\"product-desc\">{{ product.note }}</p>\n      <p class=\"product-price\">¥{{ product.price }}</p>\n\n      <div class=\"size-group\" role=\"group\" :aria-label=\"`${product.name} 尺码选择`\">\n        <button\n          v-for=\"size in sizes\"\n          :key=\"size\"\n          type=\"button\"\n          class=\"size-btn\"\n          :class=\"{ active: selectedSize === size }\"\n          :aria-pressed=\"selectedSize === size\"\n          @click=\"selectSize(size)\"\n        >\n          {{ size }}\n        </button>\n      </div>\n\n      <button type=\"button\" class=\"btn btn-primary add-btn\" @click=\"handleAdd\">\n        加入购物车\n      </button>\n    </div>\n  </article>\n</template>\n\n<style scoped>\n.product-card {\n  display: flex;\n  flex-direction: column;\n  background: #fff;\n  border: 1px solid #e6e3dc;\n  border-radius: 10px;\n  overflow: hidden;\n}\n\n.product-visual {\n  aspect-ratio: 4 / 3;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n}\n\n.product-visual-mark {\n  font-size: 2rem;\n  letter-spacing: 0.3em;\n  color: rgba(255, 255, 255, 0.85);\n  font-weight: 300;\n}\n\n.product-body {\n  padding: 18px 18px 20px;\n  display: flex;\n  flex-direction: column;\n  gap: 8px;\n  flex: 1;\n}\n\n.product-name {\n  margin: 0;\n  font-size: 1.05rem;\n  font-weight: 600;\n  color: #1f2a26;\n}\n\n.product-desc {\n  margin: 0;\n  font-size: 0.85rem;\n  color: #7a8078;\n  line-height: 1.5;\n}\n\n.product-price {\n  margin: 2px 0 6px;\n  font-size: 1.1rem;\n  font-weight: 600;\n  color: #2f6b5a;\n}\n\n.size-group {\n  display: flex;\n  gap: 8px;\n  margin-bottom: 6px;\n}\n\n.size-btn {\n  flex: 1;\n  min-width: 0;\n  padding: 8px 0;\n  font-size: 0.9rem;\n  color: #4a524c;\n  background: #f6f4ef;\n  border: 1px solid #ddd9d0;\n  border-radius: 6px;\n  cursor: pointer;\n  transition: background 0.15s, border-color 0.15s, color 0.15s;\n}\n\n.size-btn:hover {\n  border-color: #2f6b5a;\n}\n\n.size-btn.active {\n  background: #2f6b5a;\n  border-color: #2f6b5a;\n  color: #fff;\n}\n\n.size-btn:focus-visible {\n  outline: 2px solid #2f6b5a;\n  outline-offset: 2px;\n}\n\n.add-btn {\n  margin-top: auto;\n  width: 100%;\n}\n</style>\n"
  }
]
  const r = analyzeEvents(files, compiler)
  assert.equal(r.status, 'ok')
  assert.equal(r.payloadChecked, 1)
  assert.deepEqual(r.payloadAdvisories, [{kind:'MISSING_DESTRUCTURED_KEYS',file:'src/App.vue',component:'ProductSection',event:'add-to-cart',missingKeys:['product']}])
})
