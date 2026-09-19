import { _electron as electron } from '@playwright/test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
const dir = await mkdtemp(path.join(tmpdir(), 'virtualyou-desktop-test-'));
let app;
try {
  const launch = () => electron.launch({ args: ['.'], env: { ...process.env, VIRTUAL_YOU_DESKTOP_TEST_DATA: dir } });
  app = await launch();
  let page = await app.firstWindow();
  await page.getByRole('button', {name: 'Developer / preview'}).click();
  await page.getByRole('button', {name: 'Explore the preview'}).click();
  assert.deepEqual(await page.evaluate(() => [typeof window.virtualYou, typeof window.require]), ['object', 'undefined']);
  await page.getByRole('button', {name: 'Connect Slack', exact: true}).click();
  await page.getByRole('button', {name: 'Use example connection'}).click();
  await page.getByRole('button', {name: 'Continue', exact: true}).click();
  await page.getByRole('checkbox', {name: 'Include VirtualYou', exact: true}).check();
  await page.getByRole('button', {name: 'Continue', exact: true}).click();
  await page.getByRole('button', {name: 'Finish preview'}).click();
  await page.getByRole('heading', {name: 'Your workspace is taking shape.'}).waitFor();
  await app.close();
  app = await launch();
  page = await app.firstWindow();
  await page.getByRole('button', {name: 'Developer / preview'}).click();
  await page.getByRole('heading', {name: 'Your workspace is taking shape.'}).waitFor();
  await page.getByRole('button', {name: 'Try a sample approval'}).click();
  await page.getByRole('button', {name: 'Approve sample', exact: true}).click();
  await page.getByRole('dialog').getByRole('button', {name: 'Approve sample', exact: true}).click();
  assert.equal((await page.evaluate(() => window.virtualYou.snapshot())).drafts[0].status, 'simulated');
  console.log('PASS: native Electron onboarding, isolated renderer, restart persistence, and simulated approval');
} finally {
  await app?.close();
  await rm(dir, {recursive: true, force: true});
}
