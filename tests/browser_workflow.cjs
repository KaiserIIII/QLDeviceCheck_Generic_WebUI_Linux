/* Hardware-free browser acceptance. Use an existing Playwright installation.
 * NODE_PATH=/path/to/node_modules node tests/browser_workflow.cjs http://127.0.0.1:18318
 * Start the server with --demo and a disposable --data-dir first.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
  const url = process.argv[2] || 'http://127.0.0.1:18318';
  const browser = await chromium.launch({ headless: true,
    ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
  const page = await browser.newPage({ viewport: { width: 1440, height: 980 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  try {
    await page.goto(url);
    if (process.env.QLDC_BROWSER_TOKEN) {
      await page.getByRole('button', { name: '访问凭证', exact: true }).click();
      await page.getByLabel('访问令牌').fill(process.env.QLDC_BROWSER_TOKEN);
      await page.getByRole('button', { name: '保存并连接' }).click();
    }
    await page.getByRole('button', { name: '配置校验', exact: true }).click();
    await page.getByRole('button', { name: '载入当前配置' }).click();
    await page.getByRole('button', { name: '校验配置', exact: true }).click();
    await page.locator('#validation').filter({ hasText: '"valid": true' }).waitFor();
    await page.getByRole('button', { name: '设备清单', exact: true }).click();
    await page.getByText('DEMO_PLC', { exact: true }).waitFor();
    await page.getByRole('button', { name: '新建验收', exact: true }).click({ timeout: 3000 });
    await page.getByLabel('工位 / 设备编号').fill('DEMO-FAT-01');
    await page.getByLabel('批次编号').fill('DEMO-2026-10');
    await page.getByLabel('操作者').fill('演示工程师');
    await page.getByLabel('演示场景').selectOption('faults');
    await page.getByRole('button', { name: '创建验收任务', exact: true }).click();
    await page.getByTestId('task-state').filter({ hasText: '已完成' }).waitFor();
    await page.getByRole('button', { name: '查看证据' }).first().click();
    await page.getByRole('dialog', { name: '设备测试证据' }).waitFor();
    assert.ok((await page.getByRole('dialog').innerText()).includes('SIMULATED'));
    await page.getByRole('button', { name: '关闭证据' }).click();
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: '导出 JSON' }).click();
    const download = await downloadPromise;
    const saved = await download.path();
    const report = JSON.parse(await fs.readFile(saved, 'utf8'));
    assert.equal(report.mode, 'demo');
    assert.ok(report.summary.failed > 0);
    const baselineId = report.id;
    await page.getByLabel('复测场景').selectOption('healthy');
    await page.getByRole('button', { name: '复测未通过设备', exact: true }).click();
    await page.getByTestId('task-state').filter({ hasText: '已完成' }).waitFor();
    await page.getByRole('button', { name: '与原任务比较', exact: true }).click();
    await page.getByTestId('comparison').waitFor();
    assert.ok((await page.getByTestId('comparison').innerText()).includes('恢复'));
    assert.ok((await page.getByTestId('comparison').innerText()).includes('部分范围'));
    await fs.mkdir('output/playwright', { recursive: true });
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: 'output/playwright/desktop.png', fullPage: true });
    const htmlDownload = page.waitForEvent('download');
    await page.getByRole('button', { name: '导出 HTML' }).click();
    const html = await htmlDownload;
    const htmlPath = await html.path();
    const htmlReport = await fs.readFile(htmlPath, 'utf8');
    assert.ok(htmlReport.includes('仅选定设备'));
    assert.ok(htmlReport.includes('SIMULATED'));
    await fs.writeFile('output/playwright/report.html', htmlReport);
    const reportPage = await browser.newPage({ viewport: { width: 1200, height: 900 } });
    await reportPage.setContent(htmlReport);
    await reportPage.getByRole('heading', { name: '工控设备验收报告', exact: true }).waitFor();
    await reportPage.screenshot({ path: 'output/playwright/report.png', fullPage: true });
    await reportPage.close();
    await page.getByRole('button', { name: '工位总览', exact: true }).click();
    await page.getByText('DEMO-FAT-01').first().waitFor();
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: 'output/playwright/overview.png', fullPage: true });
    await page.getByRole('button', { name: '任务历史', exact: true }).click();
    await page.reload();
    await page.getByRole('button', { name: '任务历史', exact: true }).click();
    await page.getByText('DEMO-FAT-01').first().waitFor();
    assert.ok((await page.locator('body').innerText()).includes('DEMO-2026-10'));
    await page.getByLabel('时间排序').selectOption('oldest');
    await page.getByRole('button', { name: '筛选', exact: true }).click();
    await page.getByLabel('时间排序').filter({ has: page.locator('option[value="oldest"][selected]') }).waitFor();
    const ordered = await page.evaluate(async () => {
      const token = sessionStorage.getItem('qldc.token');
      return (await fetch('/api/jobs?sort=oldest&limit=20', { headers: token ? { Authorization: `Bearer ${token}` } : {} })).json();
    });
    assert.equal(await page.locator('[data-action="open"]').first().getAttribute('data-id'), ordered.jobs[0].id);
    // Actual retained tasks and comparison route, with oldest/newest on different pages.
    const crossPage = await page.evaluate(async () => {
      const token = sessionStorage.getItem('qldc.token');
      const headers = {'Content-Type': 'application/json', ...(token ? {Authorization: `Bearer ${token}`} : {})};
      const ids = [], marker = `CROSS-PAGE-${Date.now()}`;
      for (let i = 0; i < 21; i++) {
        const response = await fetch('/api/jobs', {method: 'POST', headers,
          body: JSON.stringify({station_id: 'CROSS-PAGE <script>bad()</script>', batch: marker, scenario: i === 0 ? 'faults' : 'healthy'})});
        if (!response.ok) throw Error('fixture creation failed');
        const job = (await response.json()).job;
        ids.push(job.id);
        let finished = false;
        for (let attempt = 0; attempt < 200; attempt++) {
          const saved = (await (await fetch(`/api/jobs/${job.id}`, {headers})).json()).job;
          if (['completed', 'failed', 'cancelled', 'interrupted'].includes(saved.status)) {finished = true;break;}
          await new Promise(resolve => setTimeout(resolve, 30));
        }
        if (!finished) throw Error('fixture task did not finish');
      }
      return {baseline: ids[0], current: ids[20], marker};
    });
    await page.getByLabel('搜索记录').fill(crossPage.marker);
    await page.getByRole('button', {name: '筛选', exact: true}).click();
    await page.locator('[data-action="open"]').first().waitFor();
    await page.locator(`select[name="baseline"] option[value="${crossPage.baseline}"]`).waitFor({state: 'attached'});
    await page.getByLabel('基线任务').selectOption(crossPage.baseline);
    await page.getByRole('button', {name: '下一页', exact: true}).click();
    await page.locator(`select[name="current"] option[value="${crossPage.current}"]`).waitFor({state: 'attached'});
    assert.equal(await page.getByLabel('基线任务').inputValue(), crossPage.baseline);
    await page.getByLabel('当前任务').selectOption(crossPage.current);
    await page.getByRole('button', {name: '上一页', exact: true}).click();
    await page.locator(`select[name="baseline"] option[value="${crossPage.baseline}"]`).waitFor({state: 'attached'});
    assert.equal(await page.getByLabel('当前任务').inputValue(), crossPage.current);
    await page.getByLabel('搜索记录').fill('DEMO-FAT-01');
    await page.getByRole('button', {name: '筛选', exact: true}).click();
    await page.locator('[data-action="open"]').first().waitFor();
    assert.equal(await page.getByLabel('基线任务').inputValue(), crossPage.baseline);
    assert.equal(await page.getByLabel('当前任务').inputValue(), crossPage.current);
    assert.equal(await page.locator('#compare-form script').count(), 0);
    const comparisonResponse = page.waitForResponse(response => response.url().includes('/api/compare?'));
    await page.getByRole('button', {name: '比较任务', exact: true}).click();
    const compared = await comparisonResponse;
    const comparedUrl = new URL(compared.url());
    assert.equal(comparedUrl.searchParams.get('baseline'), crossPage.baseline);
    assert.equal(comparedUrl.searchParams.get('current'), crossPage.current);
    const actualComparison = (await compared.json()).comparison;
    assert.equal(actualComparison.baseline_id, crossPage.baseline);
    assert.equal(actualComparison.current_id, crossPage.current);
    assert.equal(actualComparison.comparable, true);
    assert.equal(actualComparison.whole_unit_recovered, true);
    await page.getByTestId('comparison').waitFor();
    assert.ok((await page.getByTestId('comparison').innerText()).includes('恢复'));
    await page.screenshot({path: 'output/playwright/cross-page-comparison.png', fullPage: true});
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: 'output/playwright/mobile.png', fullPage: true });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    // A browser-only fixture verifies structured probe/carrier evidence and escaping.
    await page.route('**/api/jobs/' + baselineId, async route => {
      const response = await route.fetch();
      const payload = await response.json();
      payload.job.results[0].attempts = [{ request: '<script>window.badInjected=true</script>', response: 'UI-PROBE-RESPONSE' }];
      payload.job.results[0].supporting_checks = [{ device_id: 'UI-CARRIER', attempts: [{ response: 'UI-CARRIER-RESPONSE' }] }];
      await route.fulfill({ response, json: payload });
    });
    await page.locator(`[data-action="open"][data-id="${baselineId}"]`).click();
    await page.getByRole('button', { name: '查看证据' }).first().click();
    await page.getByText('全部探测记录 (1)', { exact: true }).click();
    await page.getByText('载体检查证据 (1)', { exact: true }).click();
    assert.ok((await page.getByRole('dialog', { name: '设备测试证据' }).innerText()).includes('UI-PROBE-RESPONSE'));
    assert.ok((await page.getByRole('dialog', { name: '设备测试证据' }).innerText()).includes('UI-CARRIER-RESPONSE'));
    assert.equal(await page.locator('#evidence-body script').count(), 0);
    assert.equal(await page.evaluate(() => window.badInjected), undefined);
    // Browser rendering/click/download behavior, with synthetic protected report
    // responses. test_legacy_reports.py separately covers the real loopback server.
    const legacy = await browser.newPage({viewport: {width: 1200, height: 900}});
    legacy.on('pageerror', e => errors.push(e.message));
    await legacy.goto(url + '/legacy');
    await legacy.evaluate(() => {
      sessionStorage.setItem('qldc.token', 'browser-fixture-token');
      renderReport({summary: {passed: 1, failed: 0}, report: {url: '/report/fixture/report.html',
        html_url: '/report/fixture/report.html', txt_url: '/report/fixture/report.txt', json_url: '/report/fixture/report.json'}});
    });
    await legacy.route('**/report/fixture/report.*', async route => {
      const authorized = route.request().headers().authorization === 'Bearer browser-fixture-token';
      const format = new URL(route.request().url()).pathname.split('.').pop();
      await route.fulfill({status: authorized ? 200 : 401,
        contentType: {html: 'text/html', txt: 'text/plain', json: 'application/json'}[format],
        body: authorized ? `synthetic browser report ${format}` : '{"ok":false,"error":"Access token required"}'});
    });
    for (const [label, format] of [['网页', 'html'], ['TXT', 'txt'], ['JSON', 'json']]) {
      const downloaded = legacy.waitForEvent('download');
      await legacy.getByRole('button', {name: label, exact: true}).click();
      const file = await downloaded;
      assert.equal(file.suggestedFilename(), `report.${format}`);
      assert.equal(await fs.readFile(await file.path(), 'utf8'), `synthetic browser report ${format}`);
      assert.ok(!file.url().includes('token'));
    }
    await legacy.evaluate(() => sessionStorage.setItem('qldc.token', 'wrong'));
    await legacy.getByRole('button', {name: '网页', exact: true}).click();
    await legacy.locator('#statusText').filter({hasText: '401'}).waitFor();
    assert.equal(await legacy.locator('a[href*="token"]').count(), 0);
    await legacy.screenshot({path: 'output/playwright/legacy-protected-report.png', fullPage: true});
    await legacy.close();
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ result: 'PASS', baselineId, crossPage, consoleErrors: errors, screenshots: ['desktop.png', 'mobile.png', 'cross-page-comparison.png', 'legacy-protected-report.png'] }));
  } finally { await browser.close(); }
})().catch(err => { console.error(err); process.exitCode = 1; });
