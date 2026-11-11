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
    return { imports, required: supportedProps ? required : null, template: d.template?.ast }
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

if (require.main === module) {
  try {
    const root = path.resolve(process.argv[2])
    const input = collectFiles(root)
    const result = analyzeFiles(input.files, input.files.length ? loadCompiler(root) : {}, input.truncated)
    result.skippedFiles += input.skippedFiles
    process.stdout.write(JSON.stringify(result))
  } catch {
    // 不返回异常正文，路径、源码和供应商数据不能经构建错误泄露。
    process.stdout.write(JSON.stringify({ version: 1, status: 'unavailable', issues: [] }))
  }
}

module.exports = { analyzeFiles, collectFiles, loadCompiler, LIMITS }
