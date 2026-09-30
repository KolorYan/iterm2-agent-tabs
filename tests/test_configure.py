#!/usr/bin/env python3
"""Runs configure.py in a throw-away $HOME and checks what it writes (never touches the real one)."""
import glob
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGURE = os.path.join(ROOT, "configure.py")
SCRIPT = "/opt/x/agent-tab-state.sh"
COMMAND = '"%s" auto' % SCRIPT
MARK = "agent-tab-state.sh"

failures = []


def check(name, cond, detail=""):
    if not cond:
        failures.append(name)
        print("FAIL", name, detail)


def run(home, *args):
    env = dict(os.environ, HOME=home)
    return subprocess.run([sys.executable, CONFIGURE, *args], env=env, capture_output=True, text=True)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def ours(groups):
    return [h for g in groups for h in g.get("hooks", []) if MARK in h.get("command", "")]


def fresh_home():
    home = tempfile.mkdtemp(prefix="cfg-test-")
    user_hook = {"hooks": [{"type": "command", "command": "echo mine"}]}
    write(os.path.join(home, ".claude/settings.json"),
          json.dumps({"model": "x", "hooks": {"Stop": [user_hook]}}))
    write(os.path.join(home, ".codex/config.toml"), "[features]\nhooks = true\n")
    return home


DYN = "Library/Application Support/iTerm2/DynamicProfiles/ai-agent-tabs.json"

# ---- install ---------------------------------------------------------------------------------
home = fresh_home()
r = run(home, "install", SCRIPT, "islands")
check("install exits 0", r.returncode == 0, r.stderr)
settings = read_json(os.path.join(home, ".claude/settings.json"))
check("other settings keys survive", settings.get("model") == "x")
check("user's own Stop hook survives",
      any(h.get("command") == "echo mine" for g in settings["hooks"]["Stop"] for h in g["hooks"]))
check("our hook on every Claude event",
      all(len(ours(settings["hooks"].get(ev, []))) == 1
          for ev in ("SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest", "Stop", "SessionEnd")))
check("hook command is the quoted script + auto",
      ours(settings["hooks"]["Stop"])[0]["command"] == COMMAND)
codex = read_json(os.path.join(home, ".codex/hooks.json"))
check("Codex hooks written", len(ours(codex["hooks"]["Stop"])) == 1)
toml = open(os.path.join(home, ".codex/config.toml"), encoding="utf-8").read()
check("Codex terminal_title added (with our marker)", 'terminal_title = ["thread-name", "project-name"]' in toml and "agent-tabs:" in toml)
check("existing [features] kept", "hooks = true" in toml)
profile = read_json(os.path.join(home, DYN))["Profiles"][0]
check("profile name/guid", profile["Name"] == "AI Agents" and profile["Guid"] == "ai-agent-tabs-7c1e6a52")
check("automatic tab icon mode", profile["Icon"] == 1)
check("badge font is a PostScript name without size", profile["Badge Font"] == "HelveticaNeue-BoldItalic")
check("islands background", abs(profile["Background Color"]["Red Component"] - 0x19 / 255) < 1e-6)
check("badge colour is translucent", abs(profile["Badge Color"]["Alpha Component"] - 0.55) < 1e-6)
backups = len(glob.glob(os.path.join(home, ".claude/settings.json.bak-*")))
check("settings.json backed up before the change", backups == 1, str(backups))

# ---- idempotent ------------------------------------------------------------------------------
r = run(home, "install", SCRIPT, "islands")
settings2 = read_json(os.path.join(home, ".claude/settings.json"))
check("second install changes nothing", settings2 == settings)
check("second install adds no duplicate hooks", all(len(ours(g)) == 1 for g in settings2["hooks"].values() if ours(g)))
check("second install makes no new backup", len(glob.glob(os.path.join(home, ".claude/settings.json.bak-*"))) == backups)
check("terminal_title not added twice", open(os.path.join(home, ".codex/config.toml")).read().count("terminal_title") == 1)

# ---- theme none ------------------------------------------------------------------------------
run(home, "install", SCRIPT, "none")
profile = read_json(os.path.join(home, DYN))["Profiles"][0]
check("theme none keeps your colours", "Background Color" not in profile and "Ansi 1 Color" not in profile)

# ---- uninstall -------------------------------------------------------------------------------
r = run(home, "uninstall", SCRIPT)
check("uninstall exits 0", r.returncode == 0, r.stderr)
settings3 = read_json(os.path.join(home, ".claude/settings.json"))
check("uninstall removes only our hooks",
      all(not ours(g) for g in settings3["hooks"].values()) and
      any(h.get("command") == "echo mine" for g in settings3["hooks"]["Stop"] for h in g["hooks"]))
check("uninstall keeps unrelated settings", settings3.get("model") == "x")
check("uninstall removes our Codex hooks", "hooks" not in read_json(os.path.join(home, ".codex/hooks.json")))
check("uninstall removes the dynamic profile", not os.path.exists(os.path.join(home, DYN)))
check("uninstall restores the Codex title", "terminal_title" not in open(os.path.join(home, ".codex/config.toml")).read())

# ---- robustness ------------------------------------------------------------------------------
home = fresh_home()
write(os.path.join(home, ".claude/settings.json"), "{ this is not json")
r = run(home, "install", SCRIPT, "islands")
check("invalid settings.json: no crash", r.returncode == 0, r.stderr)
check("invalid settings.json is left untouched", open(os.path.join(home, ".claude/settings.json")).read() == "{ this is not json")

home = fresh_home()
write(os.path.join(home, ".codex/config.toml"), '[tui]\nterminal_title = ["spinner"]\n')
run(home, "install", SCRIPT, "islands")
toml = open(os.path.join(home, ".codex/config.toml")).read()
check("user-customised Codex title is preserved", toml == '[tui]\nterminal_title = ["spinner"]\n', toml)

home = tempfile.mkdtemp(prefix="cfg-test-")
r = run(home, "install", SCRIPT, "islands")
check("no Claude/Codex present: still writes the profile", os.path.exists(os.path.join(home, DYN)) and r.returncode == 0, r.stderr)

print("failures: %d" % len(failures))
sys.exit(1 if failures else 0)
