import { test, expect } from 'claude-code/testing';
import { quietCommand } from './quiet.js';

test('adds quiet flags to install commands only', () => {
  expect(quietCommand('pnpm install --frozen-lockfile').command).toBe('pnpm install --reporter=append-only --loglevel=warn --frozen-lockfile');
  expect(quietCommand('npm ci').applied).toEqual(['npm']);
  expect(quietCommand('npm install --silent').applied).toEqual([]);
  expect(quietCommand('git diff && cat package.json').applied).toEqual([]);
  expect(quietCommand('npm run test').applied).toEqual([]);
});
