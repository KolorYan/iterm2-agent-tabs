#!/bin/bash
# iTerm2 multi-agent tab setup: the tab title shows the Claude Code / Codex session name + run state
#   Install:    bash install.sh
#   Uninstall:  bash install.sh --uninstall
#   Colors:     bash install.sh --theme islands|idea|vscode|none   (default islands = IDEA Islands Dark; none keeps your existing terminal colors)
set -e
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
DEST_DIR="$HOME/.agent-tabs"
SCRIPT="$DEST_DIR/agent-tab-state.sh"
ICON_DIR="$DEST_DIR/icons"

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
  echo "python3 is required (install it with xcode-select --install or brew install python)"; exit 1
fi

THEME="islands"
ACTION="install"
while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall) ACTION="uninstall" ;;
    --theme=*) THEME="${1#--theme=}" ;;
    --theme) [ $# -ge 2 ] && { THEME="$2"; shift; } ;;
  esac
  shift
done

# iTerm2 UI tweaks: turn off the blue "new output" dot on tabs; reduce the color difference between the sidebar and terminal background under the Minimal theme (restart iTerm2 to take effect)
ITERM_DEFAULTS="ShowNewOutputIndicator MinimalTabStyleBackgroundColorDifference DisableTabBarTooltips MinimalDeslectedColoredTabAlpha UseCustomTabBarFontSize CustomTabBarFontSize TabTitlesUseSmartTruncation"

if [ "$ACTION" = "uninstall" ]; then
  echo "==> Uninstalling"
  python3 "$SRC_DIR/configure.py" uninstall "$SCRIPT"
  for k in $ITERM_DEFAULTS; do defaults delete com.googlecode.iterm2 "$k" 2>/dev/null || true; done
  # remove a bundled icon only while it is still byte-identical to ours (a replacement you made stays)
  for f in "$SRC_DIR"/assets/icons/*.png; do
    cmp -s "$f" "$ICON_DIR/$(basename "$f")" && rm -f "$ICON_DIR/$(basename "$f")"
  done
  rmdir "$ICON_DIR" 2>/dev/null || true
  rm -f "$SCRIPT" "$DEST_DIR/aggregator.alive" "$DEST_DIR/aggregator.log" "$DEST_DIR/aggregator.pid" "$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch/agent_tabs.py"
  echo "Done. Already-open tabs need to be reopened to go back to normal."
  exit 0
fi

echo "==> Installing the state script to $DEST_DIR"
mkdir -p "$DEST_DIR"
cp "$SRC_DIR/agent-tab-state.sh" "$SCRIPT"
chmod +x "$SCRIPT"

# Tab icons for tools iTerm2 has no built-in icon for (Claude Code's is automatic). An icon you
# already put there is never overwritten.
echo "==> Installing tab icons to $ICON_DIR"
mkdir -p "$ICON_DIR"
for f in "$SRC_DIR"/assets/icons/*.png; do
  [ -e "$ICON_DIR/$(basename "$f")" ] || cp "$f" "$ICON_DIR/"
done

AUTOLAUNCH="$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch"
echo "==> Installing the tab aggregator script into iTerm2 AutoLaunch"
mkdir -p "$AUTOLAUNCH"
cp "$SRC_DIR/agent_tabs.py" "$AUTOLAUNCH/agent_tabs.py"

echo "==> Configuring hooks and the iTerm2 profile"
python3 "$SRC_DIR/configure.py" install "$SCRIPT" "$THEME"

echo "==> Adjusting iTerm2 UI settings"
defaults write com.googlecode.iterm2 ShowNewOutputIndicator -bool false
defaults write com.googlecode.iterm2 MinimalTabStyleBackgroundColorDifference -float 0.03
# The iTerm2 tab hover tooltip content is hard-coded (Name/Profile/Command, three lines) and useless, so turn it off
defaults write com.googlecode.iterm2 DisableTabBarTooltips -bool true
# Under the Minimal theme, unselected colored tabs are blended 0.5 toward the background by default, so the state color is barely visible; raise it to 0.95 (iTerm2 itself misspells Deselected)
defaults write com.googlecode.iterm2 MinimalDeslectedColoredTabAlpha -float 0.95
# Turn off "smart truncation": when several tabs share a prefix (the group prefix of a group), iTerm2 switches to truncating from the start,
# which eats the status marker at the beginning of the title first. With it off, truncation is always from the tail, so the marker always stays and tabs line up
defaults write com.googlecode.iterm2 TabTitlesUseSmartTruncation -bool false
# Tab title font size (default 11 is a bit small): enable a custom font size; the actual value is not hard-coded here, it is synced by
# sync_tab_bar_font_size() in agent_tabs.py on every iTerm2 launch, following the Profile content-area font size
defaults write com.googlecode.iterm2 UseCustomTabBarFontSize -bool true
echo "  ✓ Turned off the tab new-output blue dot and hover tooltip; sidebar closer to terminal background; unselected tab state colors more visible; tab font size follows the content area (takes effect on next launch)"

cat <<MSG

Done. Next steps (skip whatever you have already done):
  1. iTerm2 Settings → Profiles → select "AI Agents" → Other Actions → Set as Default
  2. iTerm2 Settings → Appearance → General → Tab bar location: choose Left (if you want vertical tabs)
  3. Split-pane aggregation: iTerm2 Settings → General → Magic → check Enable Python API,
     then start it from the menu Scripts → AutoLaunch → agent_tabs.py (the first time it offers to download the Python runtime; accept)
  4. Open a new tab and run claude / codex. Codex prompts on first launch to review the new hooks; choose to trust them
  Status icons: 🔵 running  🟡 waiting for you  🟢 done  🔴 error (idle shows no icon)
  After updating, restart iTerm2 (⌘Q) so the new aggregator script takes effect; Codex will prompt to review the hooks again
  Self-test: in a new tab run  $SCRIPT waiting   (restore: $SCRIPT reset)
MSG
