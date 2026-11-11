import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import test from 'node:test';
import { buildStructuredSelector, parseArguments, parseSnapshot, renderHarness, validatePreviewUrl } from './structured-edit-browser.mjs';

test('默认 dry-run，点击可关闭，参数不完整明确失败', () => {
  const options = parseArguments(['--url', 'http://localhost:8123/api/static/demo/', '--output', 'tmp/report.json']);
  assert.equal(options.execute, false);
  assert.equal(options.clickCounter, true);
  assert.equal(parseArguments(['--url', 'http://localhost:5173/api/static/demo/', '--output', 'tmp/report.json', '--no-click']).clickCounter, false);
  assert.throws(() => parseArguments(['--url', '--execute']));
});

test('拒绝公网预览、凭据、查询参数和非 HTML data URL', () => {
  for (const url of ['https://example.com/', 'http://localhost.evil/', 'http://user:pass@localhost/', 'http://localhost/?token=x', 'file:///C:/secret', 'data:application/json,{}']) {
    assert.throws(() => validatePreviewUrl(url));
  }
  assert.equal(validatePreviewUrl('http://127.0.0.1:8123/api/static/123/'), 'http://127.0.0.1:8123/api/static/123/');
});

function fixture(depth = 1) {
  const doc = { body: { tagName: 'BODY', children: [] } };
  let parent = doc.body;
  let target;
  for (let index = 0; index < depth; index++) {
    target = { ownerDocument: doc, tagName: 'DIV', isConnected: true, parentElement: parent, children: [] };
    parent.children.push({ tagName: 'SPAN' }, { tagName: 'DIV' }, target);
    parent = target;
  }
  doc.querySelectorAll = () => [target];
  return { target, doc };
}

test('定位按同标签兄弟编号，复核唯一身份，限制深度并拒绝脱离 DOM', () => {
  const { target, doc } = fixture(2);
  assert.equal(buildStructuredSelector(target), 'body > div:nth-of-type(2) > div:nth-of-type(2)');
  doc.querySelectorAll = () => [target, {}];
  assert.equal(buildStructuredSelector(target), null);
  doc.querySelectorAll = () => [{}];
  assert.equal(buildStructuredSelector(target), null);
  assert.equal(buildStructuredSelector(fixture(13).target), null);
  target.isConnected = false;
  assert.equal(buildStructuredSelector(target), null);
});

test('CLI dry-run 无浏览器也能运行且不输出预览正文', () => {
  const result = spawnSync(process.execPath, [new URL('./structured-edit-browser.mjs', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'),
    '--url', 'http://localhost:5173/api/static/private-page-text/', '--output', 'tmp/structured-edit-browser-test/report.json'], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).connectsToBrowser, false);
  assert.ok(!result.stdout.includes('private-page-text'));
});

test('execute 需要同源检查页，并在输出中提取实际测量而非脚本文本', () => {
  assert.throws(() => parseArguments(['--url', 'http://localhost:5173/api/static/demo/', '--output', 'tmp/report.json', '--execute']));
  assert.throws(() => parseArguments(['--url', 'http://localhost:8123/api/static/demo/', '--output', 'tmp/report.json', '--harness-base', 'http://localhost:5173/docs']));
  const value = { observation: 'computed-dom-snapshot', title: '验收页', counter: { before: '0', after: '1' } };
  const encoded = Buffer.from(JSON.stringify(value)).toString('base64');
  assert.deepEqual(parseSnapshot(`<pre id="acceptance-result">${encoded}</pre>`), value);
  assert.throws(() => parseSnapshot(renderHarness('http://localhost:5173/api/static/demo/')));
});
