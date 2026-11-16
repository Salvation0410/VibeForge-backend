import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { readFile, mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { spawn } from 'node:child_process'

test('排序探针拒绝无效排序、原地排序及筛选重置', { timeout: 40000 }, async () => {
  const fixture = await readFile(new URL('./reading-sort-browser.html', import.meta.url))
  const checks = await readFile(new URL('../acceptance_reading_sort_checks.mjs', import.meta.url))
  let report
  const server = createServer(async (req, res) => {
    if (req.url === '/sort.mjs') { res.setHeader('Content-Type', 'text/javascript'); res.end(checks); return }
    if (req.url === '/report' && req.method === 'POST') {
      let body = ''
      for await (const chunk of req) {
        body += chunk
        if (body.length > 64000) { res.writeHead(413); res.end(); return }
      }
      try { report = JSON.parse(body); res.end('ok') }
      catch { res.writeHead(400); res.end() }
      return
    }
    if (req.url === '/') { res.setHeader('Content-Type', 'text/html;charset=utf-8'); res.end(fixture); return }
    res.writeHead(404); res.end()
  })
  const profile = await mkdtemp(path.join(tmpdir(), 'reading-probe-'))
  let child, timer
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    child = spawn(process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe', [
      '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--disable-extensions',
      `--user-data-dir=${profile}`, '--virtual-time-budget=3000', '--dump-dom',
      `http://127.0.0.1:${server.address().port}/`,
    ], { windowsHide: true, stdio: ['ignore', 'ignore', 'ignore'] })
    // 无调试端口、真实后端、会话和模型；只运行本服务提供的固定手写夹具。
    timer = setTimeout(() => child.kill(), 30000)
    const code = await new Promise((resolve, reject) => { child.once('error', reject); child.once('close', resolve) })
    assert.equal(code, 0, 'Chrome 必须正常退出')
    assert.equal(report?.length, 4)
    assert.equal(report[0].error, null)
    assert.ok(report[0].checks.length >= 20)
    assert.ok(report[0].checks.every(c => c.passed))
    for (const [index, step] of [[1, 'ascending'], [2, 'restore-insertion'], [3, 'filter-keeps-sort']]) {
      assert.equal(report[index].error, `READING_SORT_FAILED:sort-${step}`)
      assert.equal(report[index].checks.at(-1).passed, false)
    }
  } finally {
    clearTimeout(timer)
    if (child && child.exitCode === null && child.signalCode === null) {
      const closed = new Promise(resolve => child.once('close', resolve))
      child.kill(); await closed
    }
    server.closeAllConnections()
    await new Promise(resolve => server.close(resolve))
    // 删除前核对绝对位置，只清理本测试创建的系统临时子目录。
    assert.equal(path.dirname(path.resolve(profile)), path.resolve(tmpdir()))
    assert.ok(path.basename(profile).startsWith('reading-probe-'))
    await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
  }
})
