// Takes one 2x screenshot of the Kiasi dashboard overview in one theme (light or dark) -> shot-<theme>.png.
// Real project names and paths are rewritten to generic ones before anything is drawn.
import { createRequire } from 'node:module';
const { chromium } = createRequire(import.meta.url)('playwright'); // require() honours NODE_PATH, ESM import does not
const theme = process.argv[2] || 'light';
const b = await chromium.launch().catch(() => chromium.launch({ channel: 'chrome' }));
const ctx = await b.newContext({ viewport: { width: 1400, height: 790 }, deviceScaleFactor: 2, colorScheme: theme, reducedMotion: 'reduce' });
await ctx.addInitScript(t => {
  try { localStorage.setItem('theme', t); } catch (e) {}
  const RULES = [[/mywebsite-v2-mywebsite-storefront-admin/g, 'my-app'], [/mywebsite-storefront-admin/g, 'my-app'], [/…storefront-admin/g, '…my-app'], [/storefront-admin/g, 'my-app'], [/mywebsite[\w-]*/g, 'my-app'], [/\/var\/www\/html\/kiasi/g, '/Users/dev/my-app'], [/\/var\/www\/html/g, '/Users/dev'], [/\/home\/dell/g, '/Users/dev']];
  const scrub = n => { let s = n.nodeValue, o = s; for (const [re, to] of RULES) s = s.replace(re, to); if (s !== o) n.nodeValue = s; };
  const walk = root => { const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT); let n; while ((n = w.nextNode())) scrub(n); };
  addEventListener('DOMContentLoaded', () => {
    walk(document.body);
    new MutationObserver(ms => ms.forEach(m => { if (m.type === 'characterData') scrub(m.target); m.addedNodes.forEach(n => n.nodeType === 3 ? scrub(n) : n.nodeType === 1 && walk(n)); })).observe(document.body, { childList: true, subtree: true, characterData: true });
  });
}, theme);
const p = await ctx.newPage();
await p.goto('http://127.0.0.1:8787/#overview', { waitUntil: 'networkidle' });
await p.waitForTimeout(2500);
await p.evaluate(() => window.scrollTo(0, 0));
await p.screenshot({ path: `shot-${theme}.png` });
await b.close();
console.log('ok', theme);
