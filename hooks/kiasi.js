import { registerCompact } from './compact.js';
import { registerSearch } from './search.js';
import { registerSandbox } from './sandbox.js';
import { registerQuiet } from './quiet.js';
import {
  SEARCH_TOOL, SEARCH_DESCRIPTION, SEARCH_INPUT_SCHEMA,
  RUN_TOOL, RUN_DESCRIPTION, RUN_INPUT_SCHEMA,
  DISTILL_TOOL, DISTILL_DESCRIPTION, DISTILL_INPUT_SCHEMA,
  FETCH_TOOL, FETCH_DESCRIPTION, FETCH_INPUT_SCHEMA,
} from './constants.js';

export function register(on) {
  registerCompact(on);
  registerSearch(on);
  registerSandbox(on);
  registerQuiet(on);
  on('session.start', async ($, e, next) => {
    await $.tool.register({ name: SEARCH_TOOL, description: SEARCH_DESCRIPTION, inputSchema: SEARCH_INPUT_SCHEMA });
    await $.tool.register({ name: RUN_TOOL, description: RUN_DESCRIPTION, inputSchema: RUN_INPUT_SCHEMA });
    await $.tool.register({ name: DISTILL_TOOL, description: DISTILL_DESCRIPTION, inputSchema: DISTILL_INPUT_SCHEMA });
    await $.tool.register({ name: FETCH_TOOL, description: FETCH_DESCRIPTION, inputSchema: FETCH_INPUT_SCHEMA });
    return next(e);
  });
}
