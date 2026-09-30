#!/usr/bin/env python3
"""Merge/remove the Claude Code and Codex hooks, and write the iTerm2 dynamic profile.
Usage: configure.py install|uninstall <state_script_path> [islands|idea|vscode|none]
"""
import json, os, plistlib, shutil, sys, time

MARK = "agent-tab-state.sh"
HOME = os.path.expanduser("~")
PROFILE_GUID = "ai-agent-tabs-7c1e6a52"
PROFILE_NAME = "AI Agents"
DYN_PROFILE = os.path.join(HOME, "Library/Application Support/iTerm2/DynamicProfiles/ai-agent-tabs.json")

CLAUDE_EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest",
                 "PostToolUse", "PostToolUseFailure", "Notification", "Elicitation",
                 "ElicitationResult", "Stop", "StopFailure", "SessionEnd"]
CODEX_EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest",
                "PostToolUse", "Stop", "Interrupt", "SessionEnd"]


def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if not text.strip():
        return {}
    return json.loads(text)


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new_text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            if f.read() == new_text:
                return
        backup = "%s.bak-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
        n = 1
        while os.path.exists(backup):
            backup = "%s.bak-%s-%d" % (path, time.strftime("%Y%m%d-%H%M%S"), n)
            n += 1
        shutil.copy2(path, backup)
        print("  Backed up: %s" % backup)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new_text)
    os.replace(tmp, path)


def strip_ours(hooks):
    for event in list(hooks.keys()):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept = []
        for g in groups:
            hs = [h for h in g.get("hooks", []) if MARK not in str(h.get("command", ""))]
            if hs:
                g = dict(g)
                g["hooks"] = hs
                kept.append(g)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]


def update_hooks_file(path, events, command, install, label):
    try:
        data = load_json(path)
    except ValueError as e:
        print("  ✗ %s is not valid JSON, skipping (%s)" % (path, e))
        return False
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    strip_ours(hooks)
    if install:
        for ev in events:
            hooks.setdefault(ev, []).append(
                {"hooks": [{"type": "command", "command": command, "timeout": 3}]})
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    save_json(path, data)
    print("  ✓ %s: %s" % (label, path))
    return True


def iterm_prefs():
    base = os.path.join(HOME, "Library/Preferences/com.googlecode.iterm2.plist")
    try:
        with open(base, "rb") as f:
            prefs = plistlib.load(f)
    except Exception:
        return {}
    if prefs.get("LoadPrefsFromCustomFolder") and prefs.get("PrefsCustomFolder"):
        custom = os.path.join(os.path.expanduser(prefs["PrefsCustomFolder"]), "com.googlecode.iterm2.plist")
        try:
            with open(custom, "rb") as f:
                prefs = plistlib.load(f)
        except Exception:
            pass
    return prefs


# ANSI palette of the JetBrains terminal/console (Darcula family; the Islands Dark console colors inherit from it)
JB_ANSI = ["#000000", "#F0524F", "#5C962C", "#A68A0D", "#3993D4", "#A771BF", "#00A3A3", "#808080",
           "#595959", "#FF4050", "#4FC414", "#E5BF00", "#1FB0FF", "#ED7EED", "#00E5E5", "#FFFFFF"]

# Terminal color schemes: modeled on VS Code Dark Modern / IntelliJ new UI dark / IntelliJ Islands Dark
COLOR_THEMES = {
    "vscode": {
        "Background Color": "#1F1F1F", "Foreground Color": "#CCCCCC", "Bold Color": "#E5E5E5",
        "Cursor Color": "#AEAFAD", "Cursor Text Color": "#1F1F1F",
        "Selection Color": "#264F78", "Selected Text Color": "#FFFFFF",
        "Link Color": "#4DAAFC", "Badge Color": "#3B8EEA", "Cursor Guide Color": "#2A2D2E",
        "ansi": ["#000000", "#CD3131", "#0DBC79", "#E5E510", "#2472C8", "#BC3FBC", "#11A8CD", "#E5E5E5",
                 "#666666", "#F14C4C", "#23D18B", "#F5F543", "#3B8EEA", "#D670D6", "#29B8DB", "#E5E5E5"],
    },
    "idea": {
        "Background Color": "#1E1F22", "Foreground Color": "#BCBEC4", "Bold Color": "#CED0D6",
        "Cursor Color": "#CED0D6", "Cursor Text Color": "#1E1F22",
        "Selection Color": "#214283", "Selected Text Color": "#DFE1E5",
        "Link Color": "#548AF7", "Badge Color": "#3574F0", "Cursor Guide Color": "#26282E",
        "ansi": JB_ANSI,
    },
    # IntelliJ IDEA 2026.1 "Islands Dark": colors taken from the ManyIslandsDark.theme.json / IslandSchemeDark.xml shipped with IDEA
    "islands": {
        "Background Color": "#191A1C", "Foreground Color": "#BCBEC4", "Bold Color": "#CED0D6",
        "Cursor Color": "#CED0D6", "Cursor Text Color": "#191A1C",
        "Selection Color": "#2A4371", "Selected Text Color": "#DFE1E5",
        "Link Color": "#71A1FE", "Badge Color": "#80ACE2", "Cursor Guide Color": "#1F2024",
        "ansi": JB_ANSI,
    },
}


def color_dict(hex_color, alpha=1.0):
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return {"Red Component": r, "Green Component": g, "Blue Component": b,
            "Alpha Component": alpha, "Color Space": "sRGB"}


def theme_keys(name):
    theme = COLOR_THEMES.get(name)
    if not theme:
        return {}
    keys = {"Use Separate Colors for Light and Dark Mode": False}
    for k, v in theme.items():
        if k == "ansi":
            for i, c in enumerate(v):
                keys["Ansi %d Color" % i] = color_dict(c)
        elif k == "Badge Color":
            keys[k] = color_dict(v, 0.55)
        else:
            keys[k] = color_dict(v)
    return keys


def write_profile(theme_name):
    prefs = iterm_prefs()
    default_guid = prefs.get("Default Bookmark Guid")
    parent = None
    for p in prefs.get("New Bookmarks", []) or []:
        if p.get("Guid") == default_guid and p.get("Guid") != PROFILE_GUID:
            parent = p
    if parent is None:
        # When the default profile is already this profile, reuse the parent profile recorded last time
        try:
            old = load_json(DYN_PROFILE)["Profiles"][0]
            parent = {"Name": old.get("Dynamic Profile Parent Name"),
                      "Guid": old.get("Dynamic Profile Parent GUID")}
        except Exception:
            parent = None
    profile = {
        "Name": PROFILE_NAME,
        "Guid": PROFILE_GUID,
        "Use Custom Tab Title": True,
        "Custom Tab Title": "\\(currentSession.user.agentState?)\\(currentSession.name)",
        "Allow Title Setting": True,
        # Style of the translucent watermark at the top right of the terminal. The content does not go through the profile's "Badge Text": in practice that key
        # has no effect in a dynamic profile, so agent-tab-state.sh sets it directly via OSC 1337;SetBadgeFormat
        "Badge Max Width": 0.25,
        "Badge Max Height": 0.08,
        "Badge Right Margin": 14,
        "Badge Top Margin": 10,
        # Watermark font: only the PostScript name is accepted, without a size (something like "xxx 12" is not found and silently falls back to the default font).
        # Italic only applies to Latin characters; CJK has no italic and falls back to upright
        "Badge Font": "HelveticaNeue-BoldItalic",
        # Session icon = automatic: matched by process title; iTerm2 3.7 ships the Claude Code icon (claude).
        # Tools without a built-in icon (such as codex) get a custom icon set per session by agent_tabs.py
        "Icon": 1,
        "Tags": ["AI"],
    }
    profile.update(theme_keys(theme_name))
    if parent and parent.get("Name"):
        profile["Dynamic Profile Parent Name"] = parent["Name"]
    if parent and parent.get("Guid"):
        profile["Dynamic Profile Parent GUID"] = parent["Guid"]
    os.makedirs(os.path.dirname(DYN_PROFILE), exist_ok=True)
    with open(DYN_PROFILE, "w", encoding="utf-8") as f:
        json.dump({"Profiles": [profile]}, f, ensure_ascii=False, indent=2)
    print("  ✓ iTerm2 dynamic profile '%s' (inherits from: %s, colors: %s)" % (
        PROFILE_NAME, (parent or {}).get("Name") or "iTerm2 defaults", theme_name if theme_name in COLOR_THEMES else "keep existing colors"))


def check_codex_config():
    path = os.path.join(HOME, ".codex/config.toml")
    if not os.path.exists(path):
        return
    try:
        import tomllib  # Python 3.11+
        with open(path, "rb") as f:
            cfg = tomllib.load(f)
        if cfg.get("features", {}).get("hooks") is False or cfg.get("features", {}).get("codex_hooks") is False:
            print("  ! hooks are disabled in ~/.codex/config.toml (features.hooks = false); change it to true")

    except ImportError:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        import re
        if re.search(r"^\s*(codex_)?hooks\s*=\s*false", text, re.M):
            print("  ! hooks may be disabled in ~/.codex/config.toml, please check [features] hooks")


CODEX_TITLE_LINE = 'terminal_title = ["thread-name", "project-name"]  # agent-tabs: state is shown by the iTerm2 tab icon'


def set_codex_title():
    """Strip the animation symbols from the Codex title, keeping only the thread name and project name (state is left to the tab icon). Leave it alone if the user customized it."""
    import re
    path = os.path.join(HOME, ".codex/config.toml")
    text = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            text = f.read()
    if "terminal_title" in text:
        if "agent-tabs:" not in text:
            print("  - You customized Codex tui.terminal_title, leaving it unchanged")
        return
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^\s*\[tui\]\s*(#.*)?$", line):
            lines.insert(i + 1, CODEX_TITLE_LINE)
            break
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines += ["[tui]", CODEX_TITLE_LINE]
    if os.path.exists(path):
        backup = "%s.bak-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(path, backup)
        print("  Backed up: %s" % backup)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("  ✓ Codex title simplified to 'thread name · project name': %s" % path)


def unset_codex_title():
    path = os.path.join(HOME, ".codex/config.toml")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    kept = [l for l in lines if "agent-tabs:" not in l]
    if kept != lines:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(kept) + "\n")
        print("  ✓ Restored the default Codex title")


def main():
    action, script = sys.argv[1], sys.argv[2]
    theme_name = sys.argv[3] if len(sys.argv) > 3 else "islands"
    install = action == "install"
    command = '"%s" auto' % script
    claude_dir = os.path.join(HOME, ".claude")
    codex_dir = os.path.join(HOME, ".codex")
    has_claude = os.path.isdir(claude_dir) or shutil.which("claude")
    has_codex = os.path.isdir(codex_dir) or shutil.which("codex")

    if has_claude:
        update_hooks_file(os.path.join(claude_dir, "settings.json"), CLAUDE_EVENTS, command, install, "Claude Code hooks")
        if install:
            try:
                s = load_json(os.path.join(claude_dir, "settings.json"))
            except ValueError:  # already reported (and skipped) by update_hooks_file
                s = {}
            if str(s.get("env", {}).get("CLAUDE_CODE_DISABLE_TERMINAL_TITLE", "")) not in ("", "0"):
                print("  ! CLAUDE_CODE_DISABLE_TERMINAL_TITLE is set in settings.json, tabs may not show the session name")
    else:
        print("  - Claude Code not found, skipping")

    if has_codex:
        update_hooks_file(os.path.join(codex_dir, "hooks.json"), CODEX_EVENTS, command, install, "Codex hooks")
        if install:
            check_codex_config()
            set_codex_title()
        else:
            unset_codex_title()
    else:
        print("  - Codex not found, skipping")

    if install:
        write_profile(theme_name)
    elif os.path.exists(DYN_PROFILE):
        os.remove(DYN_PROFILE)
        print("  ✓ Removed the iTerm2 dynamic profile")


if __name__ == "__main__":
    main()
