// Records the Kiasi dashboard in one theme at 2x: overview, a slow scroll, Sessions, Budget, back to Overview.
// Real project names and paths are rewritten to generic ones before anything is drawn.
import { chromium } from 'playwright';
import fs from 'node:fs';
const theme = process.argv[2] || 'light';
const dir = `raw-${theme}`; fs.rmSync(dir, { recursive: true, force: true });
const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 2200, height: 1240 }, deviceScaleFactor: 1, colorScheme: theme,
  recordVideo: { dir, size: { width: 2200, height: 1240 } } });
await ctx.addInitScript(t => {
  try { localStorage.setItem('theme', t); } catch (e) {}
  const RULES = [[/mywebsite-v2-mywebsite-storefront-admin/g, 'my-app'], [/mywebsite-storefront-admin/g, 'my-app'], [/…storefront-admin/g, '…my-app'], [/storefront-admin/g, 'my-app'], [/mywebsite[\w-]*/g, 'my-app'], [/\/var\/www\/html\/kiasi/g, '/Users/dev/my-app'], [/\/var\/www\/html/g, '/Users/dev'], [/\/home\/dell/g, '/Users/dev']];
  const scrub = n => { let s = n.nodeValue, o = s; for (const [re, to] of RULES) s = s.replace(re, to); if (s !== o) n.nodeValue = s; };
  const walk = root => { const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT); let n; while ((n = w.nextNode())) scrub(n); };
  addEventListener('DOMContentLoaded', () => {
    document.documentElement.style.zoom = '2';
    walk(document.body);
    new MutationObserver(ms => ms.forEach(m => { if (m.type === 'characterData') scrub(m.target); m.addedNodes.forEach(n => n.nodeType === 3 ? scrub(n) : n.nodeType === 1 && walk(n)); })).observe(document.body, { childList: true, subtree: true, characterData: true });
  });
}, theme);
const p = await ctx.newPage();
const pause = ms => p.waitForTimeout(ms);
const glide = async (px, steps, ms) => { for (let i = 0; i < steps; i++) { await p.mouse.wheel(0, px / steps); await pause(ms / steps); } };
await p.goto('http://127.0.0.1:8787/#overview', { waitUntil: 'networkidle' });
await p.mouse.move(1400, 600);
await pause(2200);
await glide(1040, 26, 2600);
await pause(1200);
await glide(-1040, 20, 1400);
await pause(600);
await p.click('#nav a[href="#sessions"]'); await pause(2200);
await glide(600, 16, 1500); await pause(800);
fs.writeFileSync(`text-${theme}-sessions.txt`, await p.evaluate(() => document.body.innerText));
await p.click('#nav a[href="#budget"]'); await pause(2600);
fs.writeFileSync(`text-${theme}-budget.txt`, await p.evaluate(() => document.body.innerText));
await p.click('#nav a[href="#overview"]'); await pause(2000);
await ctx.close(); await b.close();
const f = fs.readdirSync(dir).find(n => n.endsWith('.webm'));
fs.renameSync(`${dir}/${f}`, `dash-${theme}.webm`);
console.log('ok', theme);
