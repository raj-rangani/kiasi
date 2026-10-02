// Which Python to call: python3 on Linux and macOS, python or the py launcher on Windows.
// Pure helpers only: the engine never lets $ cross an import, so each hook keeps its own runPython.
export const PYTHON_CANDIDATES = [['python3'], ['python'], ['py', '-3']];
const MISSING = /not found|ENOENT|not recognized|Python was not found|no such file/i;

let chosen = null;

export function interpreterOrder() {
  return chosen ? [chosen] : PYTHON_CANDIDATES;
}

export function withInterpreter(argv, candidate) {
  return [...candidate, ...argv.slice(1)];
}

export function looksMissing(result) {
  const text = String(result.stderr || '');
  return !String(result.stdout || '').trim() && (result.exitCode === 127 || result.exitCode === 9009 || MISSING.test(text));
}

export function rememberInterpreter(candidate) {
  chosen = candidate;
}
