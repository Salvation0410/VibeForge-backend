import { execFileSync, spawnSync } from 'node:child_process';
import { mkdir, readFile, unlink, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

export function validatePreviewUrl(value) {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) ||
      !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname) ||
      url.username || url.password || url.search || url.hash) {
    throw new Error('预览地址仅允许无凭据、无查询参数的回环 HTTP 地址');
  }
  return url.href;
}

/** 与前端 structuredEdit.ts 一致，只接受真实父子路径并复核元素身份。 */
export function buildStructuredSelector(element) {
  if (!element) return null;
  const forbidden = new Set(['body', 'html', 'script', 'style', 'link', 'meta']);
  const doc = element.ownerDocument;
  if (!element.isConnected || forbidden.has(element.tagName.toLowerCase())) return null;
  const segments = [];
  let current = element;
  while (current && current !== doc.body) {
    const parent = current.parentElement;
    if (!parent || segments.length >= 12) return null;
    const siblings = Array.from(parent.children).filter(sibling => sibling.tagName === current.tagName);
    segments.unshift(`${current.tagName.toLowerCase()}:nth-of-type(${siblings.indexOf(current) + 1})`);
    current = parent;
  }
  const selector = `body > ${segments.join(' > ')}`;
  if (current !== doc.body || selector.length > 512 ||
      !/^body(?: > [a-z][a-z0-9-]{0,30}:nth-of-type\([1-9][0-9]{0,3}\)){1,12}$/.test(selector) ||
      segments.some(segment => forbidden.has(segment.split(':')[0]))) return null;
  try {
    const matches = doc.querySelectorAll(selector);
    return matches.length === 1 && matches[0] === element ? selector : null;
  } catch { return null; }
}

/** 固定白名单快照不读取 cookie、存储或网络头；图片地址删除查询参数。 */
export function renderHarness(url, clickCounter = true) {
  validatePreviewUrl(url);
  const encodedUrl = JSON.stringify(url).replace(/</g, '\\u003c');
  return `<!doctype html><html><head><meta charset="utf-8"><title>结构化编辑验收快照</title>
<style>html,body,iframe{margin:0;width:100%;height:100%;border:0}iframe{position:fixed;inset:0}pre{display:none}</style></head>
<body><iframe id="preview"></iframe><pre id="acceptance-result"></pre><script>
const buildSelector = ${buildStructuredSelector.toString()};
const frame = document.getElementById('preview');
let captured = false;
frame.addEventListener('load', async () => {
  if (captured || frame.src === 'about:blank') return;
  captured = true;
  const finish = result => {
    const bytes = new TextEncoder().encode(JSON.stringify(result));
    let text = ''; for (const byte of bytes) text += String.fromCharCode(byte);
    document.getElementById('acceptance-result').textContent = btoa(text);
  };
  try {
    await new Promise(resolve => setTimeout(resolve, 500));
    const doc = frame.contentDocument;
    if (!doc || !doc.body) throw new Error('unavailable');
    // 同源跳转也可能进入另一页面；测量必须属于本次请求的预览文档。
    if (doc.URL !== new URL(${encodedUrl}).href) throw new Error('navigated');
    const target = doc.querySelector('#edit-target');
    const style = target ? frame.contentWindow.getComputedStyle(target) : null;
    const button = doc.querySelector('[data-testid="counter"]') || doc.querySelector('#counter');
    const count = doc.querySelector('#count');
    const safeSource = value => {
      try { const source = new URL(value, doc.URL); source.username = ''; source.password = ''; source.search = ''; source.hash = ''; return source.href; }
      catch { return '[invalid-url]'; }
    };
    const snapshot = {
      observation: 'computed-dom-snapshot', title: doc.title, bodyText: doc.body.innerText,
      images: Array.from(doc.images, image => ({ src: safeSource(image.currentSrc || image.src), alt: image.alt,
        complete: image.complete, naturalWidth: image.naturalWidth, naturalHeight: image.naturalHeight,
        loaded: image.complete && image.naturalWidth > 0 })),
      target: target ? { selector: buildSelector(target), text: target.innerText,
        computed: { color: style.color, backgroundColor: style.backgroundColor, fontSize: style.fontSize, textAlign: style.textAlign } } : null,
      counter: { present: Boolean(button), enabled: button ? !button.disabled : null,
        before: count?.textContent ?? null, after: count?.textContent ?? null, clicked: false, action: 'none' }
    };
    if (${Boolean(clickCounter)} && button && !button.disabled) {
      button.click(); snapshot.counter.clicked = true; snapshot.counter.action = 'dom-click';
      await new Promise(resolve => setTimeout(resolve, 150));
      snapshot.counter.after = doc.querySelector('#count')?.textContent ?? null;
    }
    if (frame.contentDocument !== doc || doc.URL !== new URL(${encodedUrl}).href) throw new Error('navigated');
    finish(snapshot);
  } catch { finish({ observation: 'unavailable', reason: 'IFRAME_MEASUREMENT_FAILED' }); }
});
frame.src = ${encodedUrl};
</script></body></html>`;
}

export function parseSnapshot(dom) {
  const match = dom.match(/<pre id="acceptance-result">([A-Za-z0-9+/=]+)<\/pre>/);
  if (!match) throw new Error('未取得 iframe 快照；检查同源预览、页面加载和 Vite 路径');
  return JSON.parse(Buffer.from(match[1], 'base64').toString('utf8'));
}

export function parseArguments(argv) {
  const options = { execute: false, clickCounter: true };
  for (let index = 0; index < argv.length; index++) {
    const arg = argv[index];
    if (arg === '--execute') options.execute = true;
    else if (arg === '--no-click') options.clickCounter = false;
    else if (['--url', '--output', '--screenshot', '--chrome', '--harness-dir', '--harness-base'].includes(arg)) {
      const value = argv[++index];
      if (!value || value.startsWith('--')) throw new Error(`${arg} 缺少值`);
      options[arg.slice(2)] = value;
    } else throw new Error('未知参数；可用 --url --output --screenshot --chrome --harness-dir --harness-base --execute --no-click');
  }
  if (!options.url || !options.output) throw new Error('必须提供 --url 和 --output；默认仅 dry-run');
  validatePreviewUrl(options.url);
  if (options.execute && (!options['harness-dir'] || !options['harness-base'])) {
    throw new Error('execute 必须提供 --harness-dir 和同源 --harness-base');
  }
  if (options['harness-base']) {
    validatePreviewUrl(options['harness-base']);
    if (new URL(options.url).origin !== new URL(options['harness-base']).origin) {
      throw new Error('预览与验收页必须同源；通过 Vite /api/static/ 代理访问预览');
    }
  }
  return options;
}

function validateOutput(value, extension, root = ROOT) {
  const absolute = path.resolve(value);
  const relative = path.relative(root, absolute);
  if (relative.startsWith('..') || path.isAbsolute(relative) || path.extname(absolute) !== extension) {
    throw new Error(`输出必须位于仓库忽略目录并使用 ${extension} 扩展名`);
  }
  try { execFileSync('git', ['check-ignore', '--quiet', '--', relative], { cwd: root }); }
  catch { throw new Error('输出必须位于 Git 忽略目录，避免提交页面数据'); }
  return absolute;
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  const output = validateOutput(options.output, '.json');
  const screenshot = options.screenshot ? validateOutput(options.screenshot, '.png') : null;
  if (!options.execute) {
    console.log(JSON.stringify({ mode: 'dry-run', browserMode: 'headless-dump-dom', output, screenshot,
      clickCounter: options.clickCounter, observation: 'computed-dom-snapshot', connectsToBrowser: false }));
    return;
  }
  const harnessDir = path.resolve(options['harness-dir']);
  const harnessRoot = execFileSync('git', ['rev-parse', '--show-toplevel'], { cwd: harnessDir, encoding: 'utf8' }).trim();
  const name = `structured-edit-acceptance-${randomUUID()}.html`;
  const harness = validateOutput(path.join(harnessDir, name), '.html', harnessRoot);
  const harnessUrl = `${options['harness-base'].replace(/\/$/, '')}/${name}`;
  const chrome = options.chrome || path.join(process.env.ProgramFiles || 'C:/Program Files', 'Google/Chrome/Application/chrome.exe');
  if (screenshot) await mkdir(path.dirname(screenshot), { recursive: true });
  // 只创建并删除本轮随机命名的检查页；无调试端口，也不修改用户预览工程。
  await writeFile(harness, renderHarness(options.url, options.clickCounter), { flag: 'wx' });
  try {
    const args = ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
      '--window-size=1365,900', '--virtual-time-budget=10000', '--timeout=20000', '--dump-dom'];
    if (screenshot) args.push(`--screenshot=${screenshot}`);
    args.push(harnessUrl);
    const result = spawnSync(chrome, args, { encoding: 'utf8', timeout: 30000, maxBuffer: 8 * 1024 * 1024, windowsHide: true });
    // 不将 Chrome stderr 或原始 DOM 入报告；它们可能包含地址或页面源码。
    if (result.error || result.status !== 0) throw new Error('Chrome headless 执行失败或超时');
    const snapshot = parseSnapshot(result.stdout);
    if (screenshot) {
      const bytes = await readFile(screenshot);
      if (!bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) throw new Error('截图不是有效 PNG');
    }
    await mkdir(path.dirname(output), { recursive: true });
    await writeFile(output, `${JSON.stringify({ measuredAt: new Date().toISOString(), ...snapshot }, null, 2)}\n`);
    console.log(JSON.stringify({ mode: 'measured', observation: snapshot.observation, output, screenshot }));
    if (snapshot.observation === 'unavailable') process.exitCode = 1;
  } finally {
    await unlink(harness);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
