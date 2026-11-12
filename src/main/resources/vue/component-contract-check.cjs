'use strict'

// 只解析 SFC，不加载组件模块，也不执行用户提供的 props 表达式。
const fs = require('node:fs')
const path = require('node:path')
const { createRequire } = require('node:module')
const LIMITS = { entries: 20000, files: 24, chars: 12000, totalChars: 60000, bytes: 48000, issues: 4 }
const EXCLUDED = new Set(['node_modules', 'dist', 'build', 'target'])
const normalizeProp = name => name.replace(/-([a-z])/g, (_, c) => c.toUpperCase())
const kebab = name => name.replace(/\B([A-Z])/g, '-$1').toLowerCase()
const identifier = name => typeof name === 'string' && /^[$A-Za-z_][\w$-]{0,79}$/.test(name)
const reservedProp = name => name.startsWith('$') || ['key', 'ref', 'ref_for', 'ref_key', '__proto__',
  'onVnodeBeforeMount', 'onVnodeMounted', 'onVnodeBeforeUpdate', 'onVnodeUpdated', 'onVnodeBeforeUnmount', 'onVnodeUnmounted'].includes(name)

function keyOf(node) {
  if (node.computed) return null
  const key = node.key?.name ?? node.key?.value
  return identifier(key) ? key : null
}

function fields(node) {
  if (node?.type !== 'ObjectExpression') return null
  const result = new Map()
  for (const prop of node.properties) {
    const key = keyOf(prop)
    if (prop.type !== 'ObjectProperty' || !key || result.has(key)) return null
    result.set(key, prop.value)
  }
  return result
}

function isRequired(options) {
  const map = fields(options)
  // 带默认值或无法确定的选项不判缺失，避免静态检查代替运行时求值。
  return map && !map.has('default') && map.get('required')?.type === 'BooleanLiteral' && map.get('required').value === true
}

function describe(file, compiler) {
  try {
    const parsed = compiler.parse(file.content, { filename: file.path })
    const d = parsed.descriptor
    if (parsed.errors.length || d.script || d.scriptSetup?.src || d.template?.src ||
        (d.template?.lang && d.template.lang !== 'html') ||
        (d.scriptSetup?.lang && !['js', 'ts'].includes(d.scriptSetup.lang))) return null
    const body = d.scriptSetup ? compiler.babelParse(d.scriptSetup.content, {
      sourceType: 'module', plugins: d.scriptSetup.lang === 'ts' ? ['typescript'] : [],
    }).program.body : []
    const imports = new Map(), required = new Set(), declaredProps = new Set(), modelProps = new Set()
    let supportedProps = true
    for (const statement of body) {
      if (statement.type === 'ImportDeclaration') {
        for (const specifier of statement.specifiers) {
          if (['defineProps', 'defineModel', 'withDefaults'].includes(specifier.local.name)) supportedProps = false
          if (specifier.type === 'ImportDefaultSpecifier' && statement.importKind !== 'type' &&
              /^\.{1,2}\//.test(statement.source.value) && statement.source.value.endsWith('.vue')) {
            const target = path.posix.normalize(path.posix.join(path.posix.dirname(file.path), statement.source.value))
            if (!target.startsWith('../')) imports.set(specifier.local.name, target)
          }
        }
      }
      const declarations = statement.type === 'VariableDeclaration' ? statement.declarations : []
      const calls = statement.type === 'ExpressionStatement' ? [statement.expression] : declarations.map(d => d.init)
      if (declarations.some(d => ['defineProps', 'defineModel', 'withDefaults'].includes(d.id?.name))) supportedProps = false
      if (statement.type === 'FunctionDeclaration' && ['defineProps', 'defineModel', 'withDefaults'].includes(statement.id?.name)) supportedProps = false
      for (const call of calls) {
        if (call?.type !== 'CallExpression' || call.callee.type !== 'Identifier') continue
        if (call.callee.name === 'withDefaults') supportedProps = false
        if (call.callee.name === 'defineProps') {
          const props = fields(call.arguments[0])
          if (!props || call.typeParameters || call.typeArguments || declarations.some(d => d.id.type !== 'Identifier')) {
            supportedProps = false
            continue
          }
          const normalized = new Set()
          for (const [name, options] of props) {
            // 事件监听器会编译成 onX 属性，当前不模拟监听器转换或动态事件展开。
            if (reservedProp(name) || /^on[^a-z]/.test(name) || (options.type === 'ObjectExpression' && !fields(options)) ||
                (options.type === 'Identifier' && !['String', 'Number', 'Boolean', 'Array', 'Object', 'Date', 'Function', 'Symbol'].includes(options.name)) ||
                !['ObjectExpression', 'Identifier', 'ArrayExpression', 'NullLiteral'].includes(options.type)) supportedProps = false
            const declaration = fields(options)
            if (declaration?.has('required') && declaration.get('required').type !== 'BooleanLiteral') supportedProps = false
            if (normalized.has(normalizeProp(name))) supportedProps = false
            normalized.add(normalizeProp(name))
            declaredProps.add(normalizeProp(name))
            if (isRequired(options)) required.add(normalizeProp(name))
          }
        }
        if (call.callee.name === 'defineModel') {
          const named = call.arguments[0]?.type === 'StringLiteral'
          const name = named ? call.arguments[0].value : 'modelValue'
          const options = call.arguments[named ? 1 : 0]
          if (!identifier(name) || reservedProp(name) || /^on[^a-z]/.test(name) || (options && !fields(options))) supportedProps = false
          else if (isRequired(options)) required.add(normalizeProp(name))
          modelProps.add(normalizeProp(name))
          modelProps.add(name === 'modelValue' ? 'modelModifiers' : `${normalizeProp(name)}Modifiers`)
        }
      }
    }
    // defineModel 的生成 props 可能覆盖同名 defineProps；不模拟编译器的合并优先级。
    if ([...declaredProps].some(name => modelProps.has(name))) supportedProps = false
    return { imports, required: supportedProps ? required : null, template: d.template?.ast, scriptAst: body }
  } catch {
    // 不支持的语法/类型只减少覆盖；正常编译错误由 npm build 负责。
    return null
  }
}

function analyzeFiles(files, compiler, partial = false) {
  const result = { version: 1, status: 'ok', checkedComponents: 0, checkedUsages: 0,
    skippedFiles: 0, skippedUsages: 0, truncated: partial, issues: [] }
  const descriptions = new Map()
  let total = 0
  for (const file of files.slice(0, LIMITS.files)) {
    if (!file || typeof file.path !== 'string' || file.path.length > 180 || /[\x00-\x1f\x7f:]/.test(file.path) || path.posix.isAbsolute(file.path) ||
        file.path.includes('\\') || file.path.split('/').includes('..') || !file.path.endsWith('.vue') ||
        descriptions.has(file.path) || file.truncated || typeof file.content !== 'string' ||
        file.content.length > LIMITS.chars || total + file.content.length > LIMITS.totalChars) {
      result.skippedFiles++; result.truncated = true; continue
    }
    total += file.content.length
    const description = describe(file, compiler)
    descriptions.set(file.path, description)
    if (description?.required) result.checkedComponents++
    else result.skippedFiles++
  }
  if (files.length > LIMITS.files) { result.skippedFiles += files.length - LIMITS.files; result.truncated = true }
  for (const [file, description] of descriptions) {
    if (!description) continue
    const aliases = new Map()
    for (const [name, target] of description.imports) {
      for (const tag of [name, kebab(name)]) {
        // 同名别名无法唯一解析时跳过，不能任取一个组件做硬判定。
        aliases.set(tag, aliases.has(tag) && aliases.get(tag) !== target ? null : target)
      }
    }
    function visit(node, scoped = false) {
      if (!node) return
      const props = node.props || []
      scoped ||= props.some(p => p.type === 7 && ['for', 'slot', 'pre'].includes(p.name))
      if (node.type === 1 && node.tagType === 1 && !['component', 'slot', 'teleport', 'suspense', 'transition', 'transition-group', 'keep-alive'].includes(kebab(node.tag))) {
        const child = descriptions.get(aliases.get(node.tag))
        const uncertain = scoped || node.tag.length > 80 || props.some(p => p.type === 7 && ['bind', 'model'].includes(p.name) &&
          ((p.name === 'bind' && !p.arg) || (p.arg && !p.arg.isStatic)))
        if (uncertain || !child?.required) result.skippedUsages++
        else {
          const provided = new Set()
          for (const prop of props) {
            if (prop.type === 6) provided.add(normalizeProp(prop.name))
            if (prop.type === 7 && prop.name === 'bind' && prop.arg?.isStatic) provided.add(normalizeProp(prop.arg.content))
            if (prop.type === 7 && prop.name === 'model') {
              const name = prop.arg?.content || 'modelValue'
              provided.add(normalizeProp(name))
              if (prop.modifiers.length) provided.add(prop.arg ? `${normalizeProp(name)}Modifiers` : 'modelModifiers')
            }
          }
          result.checkedUsages++
          for (const name of child.required) {
            if (!provided.has(name) && result.issues.length < LIMITS.issues) result.issues.push({ file, component: node.tag, prop: name })
          }
        }
      }
      for (const child of node.children || []) visit(child, scoped)
    }
    visit(description.template)
  }
  if (result.issues.length) result.status = 'failed'
  return result
}

function collectFiles(root) {
  if (fs.lstatSync(root).isSymbolicLink()) throw Error('Symbolic root')
  root = fs.realpathSync(root)
  let entries = 0, truncated = false, skippedFiles = 0
  const paths = []
  function visit(directory, depth) {
    if (depth > 64) { truncated = true; return }
    const handle = fs.opendirSync(directory)
    try {
      let entry
      while ((entry = handle.readSync())) {
        if (++entries > LIMITS.entries) { truncated = true; return }
        if (entry.name.startsWith('.') || EXCLUDED.has(entry.name)) continue
        const full = path.join(directory, entry.name)
        const info = fs.lstatSync(full)
        if (info.isSymbolicLink()) { truncated = true; continue }
        const real = fs.realpathSync(full)
        if (!real.startsWith(root + path.sep)) { truncated = true; continue }
        if (info.isDirectory() && !entry.name.startsWith('.') && !EXCLUDED.has(entry.name)) visit(full, depth + 1)
        else if (info.isFile() && entry.name.endsWith('.vue')) paths.push(full)
        if (entries >= LIMITS.entries) { truncated = true; return }
      }
    } finally { handle.closeSync() }
  }
  visit(root, 0)
  paths.sort()
  const files = []
  if (paths.length > LIMITS.files) { truncated = true; skippedFiles += paths.length - LIMITS.files }
  for (const full of paths.slice(0, LIMITS.files)) {
    const before = fs.lstatSync(full, { bigint: true })
    if (before.isSymbolicLink() || before.size > BigInt(LIMITS.bytes)) { skippedFiles++; truncated = true; continue }
    const fd = fs.openSync(full, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0))
    try {
      const opened = fs.fstatSync(fd, { bigint: true })
      // Windows 的 lstat.dev 常为 0，而 fstat.dev 为卷标识；该平台用 inode 与实际路径归属核对。
      if (!opened.isFile() || opened.ino !== before.ino || (process.platform !== 'win32' && opened.dev !== before.dev) ||
          !fs.realpathSync(full).startsWith(root + path.sep)) { skippedFiles++; truncated = true; continue }
      const buffer = Buffer.alloc(LIMITS.bytes + 1)
      let size = 0, count
      while (size < buffer.length && (count = fs.readSync(fd, buffer, size, buffer.length - size, size))) size += count
      if (size > LIMITS.bytes) { skippedFiles++; truncated = true; continue }
      // 非 UTF-8 或增长中的超限文件不作确定性契约证据。
      const content = new TextDecoder('utf-8', { fatal: true }).decode(buffer.subarray(0, size))
      files.push({ path: path.relative(root, full).split(path.sep).join('/'), content })
    } finally { fs.closeSync(fd) }
  }
  return { files, truncated, skippedFiles }
}

function loadCompiler(root) {
  const localRequire = createRequire(path.join(root, 'package.json'))
  let compiler
  try { compiler = localRequire('@vue/compiler-sfc') } catch { compiler = localRequire('vue/compiler-sfc') }
  if (typeof compiler.parse !== 'function' || typeof compiler.babelParse !== 'function') throw Error('Unsupported compiler')
  return compiler
}

function dynamicAnchorScript(statements) {
  const calls = new Set(['ref', 'computed', 'reactive', 'readonly', 'shallowRef', 'defineProps', 'defineEmits', 'defineModel', 'withDefaults'])
  const methods = new Set(['map', 'filter', 'find', 'findIndex', 'some', 'every', 'reduce', 'includes', 'indexOf', 'join',
    'slice', 'splice', 'sort', 'push', 'pop', 'shift', 'unshift', 'concat', 'flat', 'flatMap', 'trim', 'split', 'replace',
    'toLowerCase', 'toUpperCase', 'startsWith', 'endsWith'])
  function visit(node) {
    if (!node || typeof node !== 'object') return false
    if (Array.isArray(node)) return node.some(visit)
    if (node.type === 'Identifier' && ['document', 'window', 'globalThis', 'createVNode', 'resolveComponent', 'render', 'h'].includes(node.name)) return true
    if (node.type === 'ImportDeclaration' && (node.source.value === 'vue-router' ||
        (node.source.value !== 'vue' && node.specifiers.some(s => calls.has(s.local.name))) ||
        (!node.specifiers.length && !node.source.value.endsWith('.css')))) return true
    if (['FunctionDeclaration', 'VariableDeclarator'].includes(node.type) && calls.has(node.id?.name)) return true
    if (node.type === 'CallExpression' || node.type === 'OptionalCallExpression') {
      const callee = node.callee
      if (!(callee.type === 'Identifier' && calls.has(callee.name)) &&
          !(callee.type === 'MemberExpression' && !callee.computed && methods.has(callee.property.name))) return true
    }
    if (node.type === 'NewExpression' && !(node.callee.type === 'Identifier' && ['Set', 'Map', 'Date'].includes(node.callee.name))) return true
    return Object.values(node).some(visit)
  }
  // 生命周期、外部函数和未知方法可能创建目标；只接受有限无 DOM 操作的语法。
  return visit(statements)
}

function analyzeAnchors(files, compiler, partial = false, documentContent = null, bootstrapStatic = true) {
  const result = { version: 1, status: 'ok', checkedLinks: 0, skippedLinks: 0,
    truncated: Boolean(partial), reason: 'NONE', issues: [] }
  const skip = reason => ({ ...result, checkedLinks: 0, reason, issues: [] })
  if (partial || documentContent === null || files.length > LIMITS.files) return skip('INCOMPLETE')
  const descriptions = new Map()
  let total = 0
  for (const file of files) {
    if (!file || typeof file.path !== 'string' || file.path.length > 180 || /[\x00-\x1f\x7f:\\]/.test(file.path) ||
        path.posix.isAbsolute(file.path) || file.path.split('/').includes('..') || !file.path.endsWith('.vue') ||
        descriptions.has(file.path) || file.truncated || typeof file.content !== 'string' ||
        file.content.length > LIMITS.chars || (total += file.content.length) > LIMITS.totalChars) return skip('INCOMPLETE')
    descriptions.set(file.path, { ...describe(file, compiler), content: file.content })
  }
  const root = descriptions.has('src/App.vue') ? 'src/App.vue' : descriptions.has('App.vue') ? 'App.vue' : null
  if (!root) return skip('NO_APP_TEMPLATE')
  if (!bootstrapStatic) return skip('DYNAMIC_PAGE')
  const ids = new Set(), links = [], active = new Set(), visited = new Set()
  let uncertain = false
  const supportedId = value => typeof value === 'string' && /^[A-Za-z][A-Za-z0-9_-]{0,79}$/.test(value)
  function visitTemplate(node, file, imports, scoped = false, clickHandled = false) {
    if (!node) return
    const props = node.props || []
    const attr = name => props.find(p => p.type === 6 && p.name === name)?.value?.content
    scoped ||= props.some(p => p.type === 7 && ['for', 'slot', 'pre'].includes(p.name))
    clickHandled ||= props.some(p => (p.type === 7 && p.name === 'on') ||
      (p.type === 6 && /^on/i.test(p.name)) || (p.type === 7 && p.name === 'bind' && /^on/i.test(p.arg?.content || '')))
    if (props.some(p => p.type === 7 && (['html', 'pre'].includes(p.name) ||
        !['if', 'else', 'else-if', 'for', 'show', 'on', 'model', 'bind', 'once', 'memo', 'text'].includes(p.name) ||
        (p.name === 'bind' && (!p.arg?.isStatic || ['id', 'name', 'is'].includes(p.arg.content)))))) uncertain = true
    for (const id of [attr('id'), node.tag === 'a' ? attr('name') : null]) if (supportedId(id)) ids.add(id)
    if (node.tag === 'base') uncertain = true
    if (node.tag === 'script' && (!/^\/?src\/main\.[jt]s$/.test(attr('src') || '') || attr('type') !== 'module')) uncertain = true
    if (node.type === 1 && node.tagType !== 0) {
      const target = imports?.get(node.tag) ?? imports?.get([...imports.keys()].find(name => kebab(name) === node.tag))
      if (!target || scoped) uncertain = true
      else visitFile(target)
    }
    if (node.tag === 'a') {
      const href = attr('href')
      // 只判断无点击拦截的字面同文档链接；top 和空片段有浏览器内建语义。
      if (href?.startsWith('#') && supportedId(href.slice(1)) && href.toLowerCase() !== '#top' && !clickHandled &&
          !props.some(p => p.type === 6 && ['download', 'target'].includes(p.name))) {
        links.push({ file, target: href.slice(1) })
      } else result.skippedLinks++
    }
    for (const child of node.children || []) visitTemplate(child, file, imports, scoped, clickHandled)
  }
  function visitFile(file) {
    if (active.has(file)) { uncertain = true; return }
    if (visited.has(file)) return
    visited.add(file)
    const description = descriptions.get(file)
    if (!description?.template) { uncertain = true; return }
    // DOM 创建、运行时渲染及路由不在此检查器可证明的范围。
    if (dynamicAnchorScript(description.scriptAst)) {
      uncertain = true; return
    }
    active.add(file)
    visitTemplate(description.template, file, description.imports)
    active.delete(file)
  }
  try {
    // index.html 可能提供目标或改变 URL 语义；不只根据 SFC 就断言目标缺失。
    if (typeof documentContent !== 'string' || documentContent.length > LIMITS.chars) return skip('INCOMPLETE')
    const parsed = compiler.parse(`<template>${documentContent}</template>`)
    if (parsed.errors.length || !parsed.descriptor.template?.ast) return skip('INCOMPLETE')
    visitTemplate(parsed.descriptor.template.ast, 'index.html', new Map())
    visitFile(root)
    if (uncertain) return skip('DYNAMIC_PAGE')
    result.checkedLinks = links.length
    for (const link of links) {
      if (!ids.has(link.target) && result.issues.length < LIMITS.issues &&
          !result.issues.some(issue => issue.file === link.file && issue.target === link.target)) result.issues.push(link)
    }
    if (result.issues.length) result.status = 'failed'
    return result
  } catch { return skip('DYNAMIC_PAGE') }
}

function readBoundedFile(root, relative) {
  const full = path.join(root, relative)
  let fd
  try {
    const before = fs.lstatSync(full, { bigint: true })
    if (!before.isFile() || before.isSymbolicLink() || before.size > BigInt(LIMITS.bytes)) return null
    fd = fs.openSync(full, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0))
    const opened = fs.fstatSync(fd, { bigint: true })
    if (opened.ino !== before.ino || !opened.isFile() ||
        !fs.realpathSync(full).startsWith(fs.realpathSync(root) + path.sep)) return null
    const buffer = Buffer.alloc(LIMITS.bytes + 1)
    let size = 0, count
    while (size < buffer.length && (count = fs.readSync(fd, buffer, size, buffer.length - size, size))) size += count
    const after = fs.fstatSync(fd, { bigint: true })
    if (size > LIMITS.bytes || after.size !== opened.size || after.mtimeNs !== opened.mtimeNs) return null
    return new TextDecoder('utf-8', { fatal: true }).decode(buffer.subarray(0, size))
  } catch { return null }
  finally { if (fd !== undefined) fs.closeSync(fd) }
}

const collectDocument = root => readBoundedFile(root, 'index.html')

function staticBootstrap(root, compiler, html) {
  try {
    const entry = compiler.parse(`<template>${html}</template>`).descriptor.template.ast.children
      .find(node => node.tag === 'html')
    const sources = []
    function visit(node) {
      if (node.tag === 'script') sources.push(node.props.find(p => p.type === 6 && p.name === 'src')?.value?.content)
      for (const child of node.children || []) visit(child)
    }
    // HTML 可以包含或省略 html/body 标签，始终从模板根遍历。
    visit(entry || compiler.parse(`<template>${html}</template>`).descriptor.template.ast)
    if (sources.length !== 1 || !/^\/?src\/main\.[jt]s$/.test(sources[0])) return false
    const content = readBoundedFile(root, sources[0].replace(/^\//, ''))
    if (content === null) return false
    const statements = compiler.babelParse(content, { sourceType: 'module', plugins: ['typescript'] }).program.body
    let app = false, create = false, mount = false
    for (const statement of statements) {
      if (statement.type === 'ImportDeclaration') {
        if (statement.source.value === 'vue' && statement.specifiers.length === 1 &&
            statement.specifiers[0].imported?.name === 'createApp' && statement.specifiers[0].local.name === 'createApp') create = true
        else if (statement.source.value === './App.vue' && statement.specifiers.length === 1 &&
            statement.specifiers[0].type === 'ImportDefaultSpecifier' && statement.specifiers[0].local.name === 'App') app = true
        else if (statement.specifiers.length || !/\.css$/.test(statement.source.value)) return false
      } else {
        const call = statement.type === 'ExpressionStatement' ? statement.expression : null
        if (mount || call?.type !== 'CallExpression' || call.callee.type !== 'MemberExpression' || call.callee.computed ||
            call.callee.property.name !== 'mount' || call.arguments.length !== 1 || call.arguments[0].value !== '#app') return false
        const factory = call.callee.object
        if (factory.type !== 'CallExpression' || factory.callee.name !== 'createApp' ||
            factory.arguments.length !== 1 || factory.arguments[0].name !== 'App') return false
        mount = true
      }
    }
    return app && create && mount
  } catch { return false }
}

if (require.main === module) {
  try {
    const root = path.resolve(process.argv[2])
    const input = collectFiles(root)
    const compiler = input.files.length ? loadCompiler(root) : {}
    const anchors = process.argv[3] === '--anchors'
    const documentContent = anchors ? collectDocument(root) : null
    const result = anchors ? analyzeAnchors(input.files, compiler, input.truncated, documentContent,
      staticBootstrap(root, compiler, documentContent))
      : analyzeFiles(input.files, compiler, input.truncated)
    if (!anchors) result.skippedFiles += input.skippedFiles
    process.stdout.write(JSON.stringify(result))
  } catch {
    // 不返回异常正文，路径、源码和供应商数据不能经构建错误泄露。
    process.stdout.write(JSON.stringify({ version: 1, status: 'unavailable', issues: [] }))
  }
}

module.exports = { analyzeFiles, analyzeAnchors, collectFiles, collectDocument, staticBootstrap, loadCompiler, LIMITS }
