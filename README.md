<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo-dark.svg">
    <img src="docs/logo-light.svg" alt="Kiasi" width="300">
  </picture>
</p>

<p align="center"><b>Make your Claude Code weekly limit last longer.</b></p>

<p align="center">
  <a href="https://github.com/raj-rangani/kiasi/actions/workflows/ci.yml"><img src="https://github.com/raj-rangani/kiasi/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="License: MIT">
  <img src="https://img.shields.io/badge/status-beta-orange" alt="Status: beta">
</p>

<p align="center"><a href="https://raj-rangani.github.io/kiasi/"><b>Website and full docs</b></a></p>

Every step in Claude Code re-sends the whole conversation, so what you really
pay for is **context size × number of steps**. Kiasi is a Claude Code plugin
that keeps both small, using fixed rules, no model calls and no dependencies
beyond Python's standard library. It **sends nothing anywhere**.

![Kiasi dashboard overview: re-read tokens per day before and after Kiasi was switched on](docs/overview.webp)

*Screenshot uses fictional sample data.*

## What it does

- **Caps huge outputs:** tool output over 12,000 chars is cut, with the full text saved to disk.
- **Stops runaway turns:** a turn is warned at 30 tool calls and stopped at 60, and retry loops are called out.
- **Refuses mega-pastes:** prompts over 40,000 chars are saved to disk instead of sent.
- **Prunes at compaction:** a rule-based prune keeps your prompts and Claude's replies word for word *(experimental)*.
- **Shows what it saved:** a local dashboard counts every action and the tokens it kept off your limit.

All twelve rules are listed on the [website](https://raj-rangani.github.io/kiasi/#rules).

## Quick start

1. Install the plugin in Claude Code:
   ```
   /plugin marketplace add raj-rangani/kiasi
   /plugin install kiasi@kiasi
   ```
2. Add two settings to the `env` block of `~/.claude/settings.json`
   ([why](https://raj-rangani.github.io/kiasi/#install)):
   ```json
   {
     "env": {
       "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "200000",
       "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1"
     }
   }
   ```
3. Restart Claude Code, then run `/kiasi:limits setup` so the dashboard can
   show your 5-hour and weekly plan limits.
4. Work as usual. After a session or two, run `/kiasi:dashboard` and open the
   URL it prints.

Requires Claude Code 2.1.283+ and Python 3.8+, on Linux or macOS. Commands,
configuration, privacy details and uninstall steps are in the
[reference](https://raj-rangani.github.io/kiasi/#reference).

> **Beta.** Kiasi is new and has mostly been used by one person. Please
> [open an issue](https://github.com/raj-rangani/kiasi/issues) when something
> blocks you or a number looks wrong.

## License

[MIT](LICENSE) © 2026 Raj Rangani
