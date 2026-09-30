import { registerCompact } from './compact.js';
import { registerSearch } from './search.js';
import { registerQuiet } from './quiet.js';

export function register(on) {
  registerCompact(on);
  registerSearch(on);
  registerQuiet(on);
}
