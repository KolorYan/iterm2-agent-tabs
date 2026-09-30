# iterm2-agent-tabs

**English** · [中文](README.zh-CN.md)

Turn iTerm2 into a workbench for running **many Claude Code / Codex CLI sessions at once**. One glance at the tab bar tells you which agent is working, which one is waiting for you, and which one is done.

```
 ●  feature-auth        ← waiting for you   (tab breathes purple, Dock icon bounces)
 ⠹  fix-flaky-test      ← running           (spinner in the title)
 ⋯  deploy-staging      ← finished talking, but a background shell is still running
 ✓  refactor-db         ← done
    notes               ← idle
 ▲  migrate-schema      ← error
```

## What you get

- **Live state on every tab.** Claude Code and Codex hooks feed a small script that writes the state to the tab: title glyph + tab color. Quiet states (running / done / idle) stay uncolored on purpose; only the two that need *you* — waiting and error — get a background color, so the tab you should look at jumps out.
- **The session name as the tab title**, with the agent's own spinner removed.
- **Split panes are summarised**: a tab shows the most urgent pane (waiting > error > running > background > done > idle).
- **Background work is not "done".** When the model has finished its turn but a `run_in_background` shell is still running, the tab shows `⋯` instead of a false `✓`.
- **Tabs organise themselves.** Tabs are grouped and ordered by **directory → AI tool → account → title** (case-insensitive, Chinese titles sorted by pinyin). Tabs in the same directory get a shared group color and prefix. Drag a tab yourself and it is left alone from then on; retitling a tab never makes it jump — it is re-sorted only when you switch away and back.
- **Badge watermark** in the top-right of every agent session: `Repo:`, `Branch:`, and the logged-in account (`Claude: alice`, `Codex: alice@example.com`).
- **Per-tool tab icons**: Claude Code is picked up automatically by iTerm2; Codex gets a bundled placeholder icon you can replace.
- **Recovers from missed events.** Claude Code fires no hook when you press <kbd>Esc</kbd> to interrupt; the script notices the `✳` idle mark in the terminal title and clears the stuck spinner.

## Requirements

- macOS and iTerm2 (developed and tested on 3.7.3; other versions untested) with **Settings ▸ General ▸ Magic ▸ Enable Python API**
- Python 3.8+ (for the installer; the aggregator runs in iTerm2's own Python runtime)
- Claude Code and/or Codex CLI

## Install

```sh
git clone https://github.com/KolorYan/iterm2-agent-tabs.git && cd iterm2-agent-tabs
./install.sh                 # --theme islands|idea|vscode|none   (default islands; none keeps your colors)
```

Then, once:

1. iTerm2 ▸ Settings ▸ Profiles ▸ select **AI Agents** ▸ *Other Actions* ▸ **Set as Default**
2. Settings ▸ Appearance ▸ General ▸ **Tab bar location = Left** (for vertical tabs), Theme = Minimal
3. Settings ▸ General ▸ Magic ▸ **Enable Python API**, then start the aggregator once: *Scripts ▸ AutoLaunch ▸ agent_tabs.py* (it starts by itself on later launches)
4. **Restart iTerm2** (⌘Q) so the new script and profile take effect, open a tab and run `claude` or `codex`. Codex asks you to trust the new hooks on first start.

Self-test in any agent tab: `~/.agent-tabs/agent-tab-state.sh waiting` (restore with `… reset`).

Uninstall: `./install.sh --uninstall` (your `settings.json` / `hooks.json` are backed up before every change and only our own entries are removed).

## How it works

```
Claude Code / Codex hook ──► agent-tab-state.sh ──► OSC 1337 SetUserVar (user.agentState, user.agentGroup)
                                                            │ written to the agent's own tty
                                                            ▼
                               agent_tabs.py (iTerm2 AutoLaunch, Python API)
                               summarises each tab's panes → tab title, color, order, icon
```

- `agent-tab-state.sh` maps hook events to a state, finds the tty by walking up the process tree (Codex detaches from its tty, so `/dev/tty` is not usable) and writes the iTerm2 escape sequences. It also detects background shells by looking for the `shell-snapshots` command line Claude Code uses.
- `configure.py` merges the hooks into `~/.claude/settings.json` and `~/.codex/hooks.json` (idempotent, recognises its own entries by the script name, backs up first), writes an iTerm2 dynamic profile, and sets Codex's `terminal_title`.
- `agent_tabs.py` subscribes to session variables and layout changes and re-checks everything every 60 s. Only one instance runs (pidfile lock); an old instance exits when a newer one takes over.

## Configuration

| Setting | Where | Effect |
|---|---|---|
| `AGENT_TAB_ATTENTION=0` | env | don't bounce the Dock icon when waiting for you |
| `AGENT_TAB_COLOR=0` | env | title glyph only, never change tab colors |
| `AGENT_TAB_BGWATCH=0` | env | disable background-shell detection (`Stop` always means done) |
| `AGENT_TAB_TTY=/dev/ttysNNN` | env | write to a specific terminal (debugging) |
| `BLINK_INTERVAL`, `BG_BLINK_INTERVAL`, `SPIN_INTERVAL` | top of `agent_tabs.py` | breathing / spinner speed; `0` turns the animation off |
| `PALETTES`, `GROUP_PALETTE` | top of `agent_tabs.py` | colors |
| `CUSTOM_ICON_TOOLS`, `ICON_DIR` | `agent_tabs.py` | which tools use a custom icon, and where the PNGs live (`~/.agent-tabs/icons/<tool>.png`) |

## Limitations

- iTerm2 only (it relies on iTerm2's Python API and proprietary escape sequences); macOS only (pinyin sorting uses CoreFoundation).
- Editing a dynamic profile file is **not** hot-reloaded by iTerm2; restart it, or change the running profile through the Python API.
- Codex asks you to re-trust hooks every time the hook commands change.
- The Codex icon is a generated placeholder — drop your own 128×128 PNG into `~/.agent-tabs/icons/codex.png`.
- Polyphonic Chinese characters may sort by the wrong reading.
- The code comments were translated from Chinese; if something reads oddly, please open an issue.

## Tests

```sh
python3 tests/test_agent_tabs.py    # drives agent_tabs.py against a fake iterm2 module
python3 tests/test_configure.py     # runs configure.py in a throw-away $HOME
python3 tests/test_install.py       # install / re-install / uninstall end to end (`defaults` stubbed)
```

## License

Apache-2.0 — see [LICENSE](LICENSE).
