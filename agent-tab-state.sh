#!/bin/sh
# agent-tab-state.sh -- show the running state of Claude Code / Codex on the iTerm2 tab it lives in
#   🔵 running  🟡 waiting for you  🔶 background work pending  🟢 done  🔴 error  ⚪ idle
#
# Usage (called by hooks; the hook JSON arrives on stdin):
#   agent-tab-state.sh auto                 derive the state from the hook event
#   agent-tab-state.sh running|waiting|bg|done|error|idle|reset   set it manually (for debugging)
#
# Environment variables:
#   AGENT_TAB_ATTENTION=0   do not bounce the Dock icon when confirmation is needed
#   AGENT_TAB_COLOR=0       do not change the tab color, only the title icon
#   AGENT_TAB_TTY=/dev/ttysNNN  manually choose the terminal to write to
#   AGENT_TAB_SKIP_BADGE=1  do not recompute the top-right badge when setting a state manually (used by the watchdog, see end of file)
#   AGENT_TAB_BGWATCH=0     disable background-task detection; Stop always counts as done (old behavior)

state="${1:-auto}"

# ---- Read the hook input (only needed in auto mode; if stdin is a terminal this is a manual run, skip it) ----
# Never read stdin when the state is given manually: when called from a script/background job stdin may be a pipe that never closes, and reading would hang
input=""
if [ "$state" = "auto" ] && [ ! -t 0 ]; then
  input=$(cat 2>/dev/null | tr -d '\n\r')
fi

json_get() { # json_get key  -> take the first "key": "value"
  printf '%s' "$input" | sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -n 1
}

# ---- Background task detection ----
# The Stop event only means "the model finished this turn"; it has nothing to do with shells started via run_in_background.
# If we just tick on Stop, the tab already shows "done" while a build/deploy is still running in the background, and a glance at the sidebar misjudges progress.
#
# The hook JSON has no background-task field, and the scratchpad tasks/*.output files only hold output, with no state marker
# (finished and running ones both stay around), so the only option is to look at processes: Claude Code's Bash tool always starts
#   /bin/zsh -c source <...>/shell-snapshots/snapshot-*.sh && ... && eval '<command>'
# in this form, as a direct child of the main claude process. At Stop time all foreground commands have exited,
# so any surviving process of this kind is a background task. The hook itself is a /bin/sh spawned directly by claude,
# with no shell-snapshots in its command line, so it cannot match itself; the watchdog is a child of the hook (a grandchild of claude),
# so it is not in the direct-child list of pgrep -P either. Self-contained processes like caffeinate are excluded naturally by their signature strings.
claude_pid() {
  [ -n "${CLAUDE_PID:-}" ] && { printf '%s' "$CLAUDE_PID"; return 0; }
  # Fallback: the hook is spawned directly by claude, so its parent is claude; for manual runs walk up the process tree
  p=$$; n=0
  while [ -n "$p" ] && [ "$p" -gt 1 ] 2>/dev/null && [ "$n" -lt 15 ]; do
    case "$(ps -o command= -p "$p" 2>/dev/null)" in
      *claude*) printf '%s' "$p"; return 0 ;;
    esac
    p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' '); n=$((n + 1))
  done
  return 1
}

has_background_shell() {
  [ "${AGENT_TAB_BGWATCH:-1}" = "0" ] && return 1
  cpid=$(claude_pid) || return 1
  for kid in $(pgrep -P "$cpid" 2>/dev/null); do
    [ "$kid" = "$$" ] && continue
    case "$(ps -o command= -p "$kid" 2>/dev/null)" in
      *shell-snapshots*) return 0 ;;
    esac
  done
  return 1
}

# ---- Exit directly in non-iTerm2 terminals (the hook may not get these variables; if it doesn't, carry on as usual) ----
case "${TERM_PROGRAM:-}" in
  ""|iTerm.app|tmux) ;;
  *) [ "${LC_TERMINAL:-}" = "iTerm2" ] || exit 0 ;;
esac

# ---- Infer the state from the hook event ----
refresh_badge=1   # always refresh on manual calls; in auto mode only low-frequency events refresh
if [ "$state" = "auto" ]; then
  refresh_badge=0
  event=$(json_get hook_event_name)
  case "$event" in
    SessionStart|UserPromptSubmit) refresh_badge=1 ;;
  esac
  case "$event" in
    SessionStart)                         state=idle ;;
    UserPromptSubmit|PostToolUse|PostToolUseFailure|ElicitationResult) state=running ;;
    PreToolUse)
      case "$(json_get tool_name)" in
        AskUserQuestion|ExitPlanMode|request_user_input) state=waiting ;;
        *) state=running ;;
      esac ;;
    PermissionRequest|Elicitation)        state=waiting ;;
    Notification)
      case "$(json_get notification_type)" in
        permission_prompt|elicitation_dialog|elicitation_url_dialog) state=waiting ;;
        idle_prompt) state=done ;;
        *) exit 0 ;;
      esac ;;
    Stop)                                 state=done ;;
    StopFailure)                          state=error ;;
    Interrupt)                            state=idle ;;
    SessionEnd)                           state=reset ;;
    *) exit 0 ;;
  esac
fi

[ "${AGENT_TAB_SKIP_BADGE:-0}" = "1" ] && refresh_badge=0

# Before deciding "done", check whether background tasks are still hanging around: if so, downgrade to bg and start a watchdog to wait until they really finish.
# Manual calls of done take the same path: if new tasks have started by the time the watchdog sends the final update, it should stay bg instead of ticking.
need_watchdog=0
if [ "$state" = "done" ] && has_background_shell; then
  state=bg
  need_watchdog=1
fi

case "$state" in
  running) icon="🔵" ;;
  waiting) icon="🟡" ;;
  bg)      icon="🔶" ;;
  done)    icon="🟢" ;;
  error)   icon="🔴" ;;
  idle)    icon="⚪" ;;
  reset)   icon="" ;;
  *) echo "unknown state: $state" >&2; exit 0 ;;
esac

# ---- Find the terminal device the agent runs on: walk up the process tree to the first process that has a tty ----
find_tty() {
  if [ -n "${AGENT_TAB_TTY:-}" ]; then echo "$AGENT_TAB_TTY"; return 0; fi
  pid=$$
  n=0
  while [ -n "$pid" ] && [ "$pid" -gt 1 ] 2>/dev/null && [ "$n" -lt 15 ]; do
    t=$(ps -o tty= -p "$pid" 2>/dev/null | tr -d ' ')
    case "$t" in
      ""|"?"|"??") ;;
      *) echo "/dev/$t"; return 0 ;;
    esac
    pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
    n=$((n + 1))
  done
  return 1
}
tty_dev=$(find_tty) || exit 0
[ -w "$tty_dev" ] || exit 0

ESC=$(printf '\033')
BEL=$(printf '\007')
osc() { printf '%s]%s%s' "$ESC" "$1" "$BEL"; }

# ---- git helpers ----
git_q() { d="$1"; shift; git -C "$d" --no-optional-locks "$@" 2>/dev/null; }
branch_of() { git_q "$1" symbolic-ref --short HEAD || git_q "$1" rev-parse --short HEAD; }
is_linked_worktree() {   # a linked-out worktree: --git-dir points to <main repo>/.git/worktrees/<name>
  gd=$(git_q "$1" rev-parse --git-dir); cm=$(git_q "$1" rev-parse --git-common-dir)
  [ -n "$gd" ] && [ -n "$cm" ] && [ "$gd" != "$cm" ]
}
sub_repos() {   # sub-repos in a multi-repo workspace, list at most two (enough to tell whether the branches agree)
  n=0
  for _s in "$1"/*/ "$1"/*/*/; do
    [ -e "$_s.git" ] || continue
    printf '%s\n' "${_s%/}"
    n=$((n + 1)); [ "$n" -ge 2 ] && return 0
  done
}

# Resolve the directory this session is really working in. A hand-made worktree workspace copy (one linked
# worktree per sub-repo inside one copy) cannot be detected from the hook's cwd, only via the marker file the statusline uses, so the resolution order matches it:
#   1) first line of ~/.claude/session-worktree/<session_id>
#   2) cwd in the hook JSON
resolve_dir() {
  sid=$(json_get session_id)
  if [ -n "$sid" ]; then
    m="$HOME/.claude/session-worktree/$sid"
    if [ -f "$m" ]; then
      wt=$(sed -n 1p "$m" 2>/dev/null)
      if [ -n "$wt" ] && [ -d "$wt" ]; then printf '%s' "$wt"; return 0; fi
    fi
  fi
  d=$(json_get cwd)
  [ -n "$d" ] && [ -d "$d" ] || d="$PWD"
  printf '%s' "$d"
}

# Describe a directory: output two lines, "<name>" and "<branch>" (only one line if there is no branch).
# If the directory itself is a repo, use it directly; if it is the root of a multi-repo workspace, take the branch from the sub-repos (mark ≠ if the two sub-repos are on different branches).
# For a linked worktree (or when the sub-repo is one), prefix the name with ⑂.
describe_dir() {
  dir="$1"
  [ -n "$dir" ] && [ -d "$dir" ] || dir="$PWD"
  br=""
  if git_q "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    top=$(git_q "$dir" rev-parse --show-toplevel); [ -n "$top" ] && dir="$top"
    name=$(basename "$dir")
    br=$(branch_of "$dir")
    is_linked_worktree "$dir" && name="⑂ $name"
  else
    name=$(basename "$dir")
    # The parent directory also holds multiple repos, so the current directory is just one level inside a multi-repo workspace (e.g. myorg/core);
    # a bare basename cannot tell which project it belongs to, so include the parent directory in the name. With 0-1 repos in the parent, leave it out,
    # so that a normal workspace root like Project/myorg does not get a useless extra path segment.
    parent=$(dirname "$dir")
    c=0
    for _p in "$parent"/*/; do
      [ -e "$_p.git" ] && c=$((c + 1))
      [ "$c" -ge 2 ] && break
    done
    [ "$c" -ge 2 ] && name="$(basename "$parent")/$name"
    subs=$(sub_repos "$dir")
    s1=$(printf '%s' "$subs" | sed -n 1p)
    s2=$(printf '%s' "$subs" | sed -n 2p)
    if [ -n "$s1" ]; then
      br=$(branch_of "$s1")
      is_linked_worktree "$s1" && name="⑂ $name"
      if [ -n "$s2" ]; then
        b2=$(branch_of "$s2")
        [ -n "$b2" ] && [ "$b2" != "$br" ] && br="$br ≠"
      fi
    fi
  fi
  if [ -n "$br" ]; then printf '%s\n%s' "$name" "$br"; else printf '%s' "$name"; fi
}

# ---- Group info: sessions in the same working directory (or the same worktree copy) form a group ----
# Output "<absolute dir><TAB><group name>"; group name = last two path segments + branch
group_info() {
  dir="$1"
  [ -n "$dir" ] && [ -d "$dir" ] || dir="$PWD"
  label="$(basename "$(dirname "$dir")")/$(basename "$dir")"
  br=$(printf '%s' "$(describe_dir "$dir")" | sed -n 2p)
  [ -n "$br" ] && label="$label@$br"
  # The third field is the AI tool (claude/codex, second dimension), the fourth is that tool's login account (third dimension);
  # agent_tabs.py uses them to nest and color sessions within the same directory; empty when they cannot be determined or read
  kind=$(agent_kind)
  printf '%s\t%s\t%s\t%s' "$dir" "$label" "$kind" "$(account_name "$kind")"
}

# ---- Badge content: line 1 worktree / repo name, line 2 branch, line 3 login account of the current CLI; each line gets a field-name prefix ----
# The prefix is added to the badge only; describe_dir's raw output (group_info takes the branch from line 2) stays unchanged

# Who triggered this hook: first look at transcript_path in the hook JSON (codex lives in ~/.codex, claude in ~/.claude);
# for manual calls without JSON, walk up the process tree to find a claude / codex process. If it cannot be determined, the account line is not shown.
agent_kind() {
  case "$(json_get transcript_path)" in
    */.codex/*)  printf codex;  return 0 ;;
    */.claude/*) printf claude; return 0 ;;
  esac
  p=$$; n=0
  while [ -n "$p" ] && [ "$p" -gt 1 ] 2>/dev/null && [ "$n" -lt 15 ]; do
    case "$(ps -o command= -p "$p" 2>/dev/null)" in
      *codex*)  printf codex;  return 0 ;;
      *claude*) printf claude; return 0 ;;
    esac
    p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' '); n=$((n + 1))
  done
  return 1
}

# Current login account (the part of the email before @, since the badge width is limited). Read the local login file directly; do not spawn claude/codex (too slow).
# Empty output for API-key login, not logged in, or unreadable, in which case the badge omits this line.
account_name() {   # account_name <claude|codex> -> login account (no prefix); empty output when unreadable
  case "$1" in
    claude)
      f="$HOME/.claude.json"; [ -n "${CLAUDE_CONFIG_DIR:-}" ] && f="$CLAUDE_CONFIG_DIR/.claude.json"
      python3 -c 'import json,sys;print(((json.load(open(sys.argv[1])).get("oauthAccount") or {}).get("emailAddress") or "").split("@")[0])' "$f" 2>/dev/null ;;
    codex)
      f="${CODEX_HOME:-$HOME/.codex}/auth.json"
      python3 -c 'import json,sys,base64;a=json.load(open(sys.argv[1]));t=(a.get("tokens") or {}).get("id_token") or "";p=t.split(".")[1];p+="="*(-len(p)%4);print((json.loads(base64.urlsafe_b64decode(p)).get("email") or "").split("@")[0])' "$f" 2>/dev/null ;;
  esac
}

account_label() {   # badge line 3: "Claude: alice" / "Codex: alice@example.com"
  kind=$(agent_kind) || return 0
  who=$(account_name "$kind")
  [ -n "$who" ] || return 0
  case "$kind" in
    claude) printf 'Claude: %s' "$who" ;;
    codex)  printf 'Codex: %s' "$who" ;;
  esac
}

badge_text() {
  describe_dir "$1" | awk 'NR==1{printf "Repo: %s", $0} NR==2{printf "\nBranch: %s", $0}'
  who=$(account_label); [ -n "$who" ] && printf '\n%s' "$who"
  return 0
}


label=""
[ -n "$icon" ] && label="$icon "
b64=$(printf '%s' "$label" | base64 | tr -d '\n')

seq_b64=$(printf '%s' "$$.$(date +%s)" | base64 | tr -d '\n')
# agentState: state of this pane; agentSeq: changes every time, tells the aggregator script in iTerm2 to refresh
seq="$(osc "1337;SetUserVar=agentState=$b64")$(osc "1337;SetUserVar=agentSeq=$seq_b64")"

# Translucent watermark at the top right of the terminal. The profile's "Badge Text" has no effect on dynamic profiles, so an escape sequence is the only way;
# on reset (session end) send an empty string to wipe the badge
if [ "$state" = "reset" ]; then
  seq="$seq$(osc "1337;SetBadgeFormat=")$(osc "1337;SetUserVar=agentGroup=")"
elif [ "$refresh_badge" = "1" ]; then
  cwd_hint=$(resolve_dir)
  badge_b64=$(badge_text "$cwd_hint" | base64 | tr -d '\n')
  group_b64=$(group_info "$cwd_hint" | base64 | tr -d '\n')
  seq="$seq$(osc "1337;SetBadgeFormat=$badge_b64")$(osc "1337;SetUserVar=agentGroup=$group_b64")"
fi

# The aggregator script (agent_tabs.py) sets the tab color centrally at runtime, so it is not set here, to avoid flicker
aggregator_alive() {
  hb="$HOME/.agent-tabs/aggregator.alive"
  [ -f "$hb" ] && [ -n "$(find "$hb" -mmin -1 2>/dev/null)" ]
}

if [ "${AGENT_TAB_COLOR:-1}" != "0" ] && ! aggregator_alive; then
  # Soft palette (consistent with agent_tabs.py, taken from the IDEA Islands color scale); dark mode uses darker colors
  if [ "$(defaults read -g AppleInterfaceStyle 2>/dev/null)" = "Dark" ]; then
    case "$state" in
      running) rgb="16 50 63" ;;    waiting) rgb="46 31 74" ;;
      error) rgb="58 21 32" ;;      *) rgb="" ;;   # no color for done/idle/background-pending
    esac
  else
    case "$state" in
      running) rgb="220 238 243" ;; waiting) rgb="232 224 245" ;;
      error) rgb="247 221 226" ;;   *) rgb="" ;;
    esac
  fi
  if [ -n "$rgb" ]; then
    set -- $rgb
    seq="$seq$(osc "6;1;bg;red;brightness;$1")$(osc "6;1;bg;green;brightness;$2")$(osc "6;1;bg;blue;brightness;$3")"
  else
    seq="$seq$(osc "6;1;bg;*;default")"
  fi
fi

if [ "$state" = "waiting" ] && [ "${AGENT_TAB_ATTENTION:-1}" != "0" ]; then
  seq="$seq$(osc "1337;RequestAttention=once")"
fi

# Inside tmux the sequence needs a passthrough wrapper (tmux must have allow-passthrough on)
if [ -n "${TMUX:-}" ]; then
  inner=$(printf '%s' "$seq" | sed "s/$ESC/$ESC$ESC/g")
  seq="${ESC}Ptmux;${inner}${ESC}\\"
fi

printf '%s' "$seq" 2>/dev/null > "$tty_dev" || true

# ---- Watchdog: wait until the background tasks really finish, then turn the tab from ⋯ into ✓ ----
# agent_tabs.py is purely event-driven (the spinner only draws animation frames and never recomputes state), so when background tasks finish
# with no new hook event arriving (e.g. the session is already idle waiting for your input), the ⋯ would stay forever.
# So fork a polling process here to watch, and send one more done once all background shells under claude have exited.
# Two things must be passed down explicitly:
#   tty  -- after the hook exits this process is reparented to init, and the terminal can no longer be found via the process tree
#   cpid -- likewise, the process-tree fallback in claude_pid() is broken too, so the value has to be frozen
if [ "$need_watchdog" = "1" ]; then
  pidf="$HOME/.agent-tabs/bgwatch.$(basename "$tty_dev").pid"
  running_watch=0
  if [ -f "$pidf" ]; then
    old=$(cat "$pidf" 2>/dev/null)
    [ -n "$old" ] && kill -0 "$old" 2>/dev/null && running_watch=1   # someone is already watching this terminal
  fi
  if [ "$running_watch" = "0" ]; then
    CLAUDE_PID=$(claude_pid); export CLAUDE_PID
    (
      n=0
      # Poll every 5 seconds, for at most 2 hours; give up on timeout so we do not leave an orphan process that never exits
      while [ "$n" -lt 1440 ]; do
        sleep 5
        # claude itself has exited: the tty may now belong to another session, do not touch its title
        kill -0 "$CLAUDE_PID" 2>/dev/null || exit 0
        has_background_shell || break
        n=$((n + 1))
      done
      AGENT_TAB_TTY="$tty_dev" AGENT_TAB_SKIP_BADGE=1 "$0" done
    ) </dev/null >/dev/null 2>&1 &
    echo $! > "$pidf"
  fi
fi
exit 0
