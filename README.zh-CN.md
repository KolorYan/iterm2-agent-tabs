# iterm2-agent-tabs

[English](README.md) · **中文**

把 iTerm2 变成同时跑**很多个 Claude Code / Codex CLI 会话**的工作台。扫一眼 tab 栏，就知道哪个 agent 在干活、哪个在等你、哪个已经做完。

```
 ●  feature-auth        ← 等你确认   （tab 呼吸闪紫色，Dock 图标跳动）
 ⠹  fix-flaky-test      ← 运行中     （标题里有转圈）
 ⋯  deploy-staging      ← 这一轮说完了，但后台 shell 还在跑
 ✓  refactor-db         ← 已完成
    notes               ← 空闲
 ▲  migrate-schema      ← 出错
```

## 功能

- **每个 tab 实时显示状态。** Claude Code 和 Codex 的 hooks 调用一个小脚本，把状态写到 tab 上：标题字形加 tab 颜色。安静的状态（运行中 / 已完成 / 空闲）故意不上色，只有两个需要*你*动手的状态，等确认和出错，才有底色，该看的那个 tab 自然跳出来。
- **tab 标题就是会话名**，并去掉 agent 自带的转圈动画。
- **分屏自动汇总**：一个 tab 显示最紧急的那个窗格（等确认 > 出错 > 运行中 > 后台 > 已完成 > 空闲）。
- **后台还有活不算"完成"**：模型这一轮说完了，但 `run_in_background` 起的 shell 还没退出时，tab 显示 `⋯`，而不是误导人的 `✓`。
- **tab 自动整理。** 按**目录 → AI 工具 → 账号 → 标题**分组排序（不区分大小写，中文标题按拼音）。同一目录的 tab 有统一的组色和组前缀。你手动拖过的 tab 之后不再被自动排序；改标题也不会让 tab 乱跳，只有切走再切回来时才重新整理。
- **右上角水印**：每个 agent 会话显示 `Repo:`、`Branch:` 和当前登录的账号（`Claude: alice`、`Codex: alice@example.com`）。
- **按工具区分的 tab 图标**：Claude Code 由 iTerm2 自动识别；Codex 用自带的占位图标，可以替换。
- **漏掉事件也能自愈。** Claude Code 按 <kbd>Esc</kbd> 打断时不会触发任何 hook；脚本会发现终端标题里的 `✳` 空闲标记，把卡住的转圈清掉。

## 要求

- macOS 和 iTerm2（开发并测试于 3.7.3，其他版本未测试），并开启 **Settings ▸ General ▸ Magic ▸ Enable Python API**
- Python 3.8+（安装脚本用；汇总脚本跑在 iTerm2 自带的 Python 运行时里）
- Claude Code 和/或 Codex CLI

## 安装

```sh
git clone https://github.com/KolorYan/iterm2-agent-tabs.git && cd iterm2-agent-tabs
./install.sh                 # --theme islands|idea|vscode|none   （默认 islands；none 保留你原来的配色）
```

然后做一次：

1. iTerm2 ▸ Settings ▸ Profiles ▸ 选中 **AI Agents** ▸ *Other Actions* ▸ **Set as Default**
2. Settings ▸ Appearance ▸ General ▸ **Tab bar location 选 Left**（垂直 tab），Theme 选 Minimal
3. Settings ▸ General ▸ Magic ▸ **Enable Python API**，然后启动一次汇总脚本：*Scripts ▸ AutoLaunch ▸ agent_tabs.py*（之后每次启动 iTerm2 会自动运行）
4. **重启 iTerm2**（⌘Q）让新脚本和 profile 生效，新开 tab 运行 `claude` 或 `codex`。Codex 首次启动会让你信任新的 hooks。

在任意 agent tab 里自测：`~/.agent-tabs/agent-tab-state.sh waiting`（恢复：`… reset`）。

卸载：`./install.sh --uninstall`（每次修改前都会备份你的 `settings.json` / `hooks.json`，只移除我们自己的条目）。

## 原理

```
Claude Code / Codex hook ──► agent-tab-state.sh ──► OSC 1337 SetUserVar (user.agentState, user.agentGroup)
                                                            │ 写到 agent 自己的 tty
                                                            ▼
                               agent_tabs.py（iTerm2 AutoLaunch，Python API）
                               汇总每个 tab 的窗格 → tab 标题、颜色、顺序、图标
```

- `agent-tab-state.sh` 把 hook 事件映射成状态，沿进程树往上找 tty（Codex 会脱离自己的 tty，`/dev/tty` 不可用），再写 iTerm2 转义序列。它还通过识别 Claude Code 起后台命令时用的 `shell-snapshots` 命令行来发现后台 shell。
- `configure.py` 把 hooks 合并进 `~/.claude/settings.json` 和 `~/.codex/hooks.json`（幂等，靠脚本名识别自己的条目，写前备份），写入 iTerm2 动态 profile，并设置 Codex 的 `terminal_title`。
- `agent_tabs.py` 订阅会话变量和布局变化，并每 60 秒全量校正一次。同一时刻只有一个实例（pid 文件锁），新实例接管后旧实例会自己退出。

## 配置

| 设置 | 位置 | 作用 |
|---|---|---|
| `AGENT_TAB_ATTENTION=0` | 环境变量 | 等你确认时不让 Dock 图标跳动 |
| `AGENT_TAB_COLOR=0` | 环境变量 | 只改标题字形，不改 tab 颜色 |
| `AGENT_TAB_BGWATCH=0` | 环境变量 | 关掉后台 shell 检测（`Stop` 一律按已完成） |
| `AGENT_TAB_TTY=/dev/ttysNNN` | 环境变量 | 指定写入的终端（调试用） |
| `BLINK_INTERVAL`、`BG_BLINK_INTERVAL`、`SPIN_INTERVAL` | `agent_tabs.py` 顶部 | 呼吸和转圈速度，设成 `0` 关闭动画 |
| `PALETTES`、`GROUP_PALETTE` | `agent_tabs.py` 顶部 | 配色 |
| `CUSTOM_ICON_TOOLS`、`ICON_DIR` | `agent_tabs.py` | 哪些工具用自定义图标，以及 PNG 所在位置（`~/.agent-tabs/icons/<tool>.png`） |

## 局限

- 只支持 iTerm2（依赖它的 Python API 和私有转义序列），只支持 macOS（拼音排序用了 CoreFoundation）。
- 直接改动态 profile 文件**不会**被 iTerm2 热加载，需要重启，或通过 Python API 改运行中的 profile。
- hook 命令一变，Codex 就会要求你重新信任 hooks。
- Codex 图标是生成的占位图，把你自己的 128×128 PNG 放到 `~/.agent-tabs/icons/codex.png` 即可替换。
- 多音字可能按错误读音排序。
- 代码注释是从中文翻译的，如有读起来别扭的地方，欢迎提 issue。

## 测试

```sh
python3 tests/test_agent_tabs.py    # 用假的 iterm2 模块驱动 agent_tabs.py
python3 tests/test_configure.py     # 在一次性的 $HOME 里运行 configure.py
python3 tests/test_install.py       # 安装 / 重装 / 卸载端到端（`defaults` 用桩替代）
```

## 许可证

Apache-2.0，见 [LICENSE](LICENSE)。
