#!/usr/bin/env python3
"""End-to-end test of install.sh in a throw-away $HOME, with `defaults` stubbed so the real iTerm2
preferences are never touched."""
import filecmp
import json
import os
import stat
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failures = []


def check(name, cond, detail=""):
    if not cond:
        failures.append(name)
        print("FAIL", name, detail)


home = tempfile.mkdtemp(prefix="inst-test-")
stubs = tempfile.mkdtemp(prefix="inst-stubs-")
log = os.path.join(stubs, "defaults.log")
with open(os.path.join(stubs, "defaults"), "w") as f:
    f.write('#!/bin/sh\necho "$@" >> "%s"\n' % log)
os.chmod(os.path.join(stubs, "defaults"), 0o755)
os.makedirs(os.path.join(home, ".claude"))
with open(os.path.join(home, ".claude/settings.json"), "w") as f:
    json.dump({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}, f)


def run(*args):
    env = dict(os.environ, HOME=home, PATH=stubs + os.pathsep + os.environ["PATH"])
    return subprocess.run(["bash", os.path.join(ROOT, "install.sh"), *args], env=env, capture_output=True, text=True)


state = os.path.join(home, ".agent-tabs/agent-tab-state.sh")
agg = os.path.join(home, "Library/Application Support/iTerm2/Scripts/AutoLaunch/agent_tabs.py")
icon = os.path.join(home, ".agent-tabs/icons/codex.png")
profile = os.path.join(home, "Library/Application Support/iTerm2/DynamicProfiles/ai-agent-tabs.json")

# ---- install ---------------------------------------------------------------------------------
r = run()
check("install exits 0", r.returncode == 0, r.stdout[-300:] + r.stderr[-300:])
check("state script installed and executable", os.path.isfile(state) and os.stat(state).st_mode & stat.S_IXUSR)
check("aggregator installed into AutoLaunch", os.path.isfile(agg) and filecmp.cmp(agg, os.path.join(ROOT, "agent_tabs.py"), shallow=False))
check("bundled Codex icon installed", os.path.isfile(icon) and filecmp.cmp(icon, os.path.join(ROOT, "assets/icons/codex.png"), shallow=False))
check("dynamic profile written", os.path.isfile(profile))
settings = json.load(open(os.path.join(home, ".claude/settings.json")))
check("hooks merged, user's hook kept",
      any("agent-tab-state.sh" in h["command"] for g in settings["hooks"]["Stop"] for h in g["hooks"]) and
      any(h["command"] == "echo mine" for g in settings["hooks"]["Stop"] for h in g["hooks"]))
calls = open(log).read()
check("iTerm2 defaults written (stubbed)", "write com.googlecode.iterm2 ShowNewOutputIndicator -bool false" in calls
      and "TabTitlesUseSmartTruncation -bool false" in calls, calls)

# ---- a custom icon survives a re-install -----------------------------------------------------
with open(icon, "wb") as f:
    f.write(b"my own icon")
run()
check("re-install keeps the icon you replaced", open(icon, "rb").read() == b"my own icon")

# ---- uninstall -------------------------------------------------------------------------------
r = run("--uninstall")
check("uninstall exits 0", r.returncode == 0, r.stderr[-300:])
check("state script removed", not os.path.exists(state))
check("aggregator removed", not os.path.exists(agg))
check("profile removed", not os.path.exists(profile))
check("your replaced icon is kept on uninstall", os.path.exists(icon) and open(icon, "rb").read() == b"my own icon")
settings = json.load(open(os.path.join(home, ".claude/settings.json")))
check("only our hooks removed", all("agent-tab-state.sh" not in h["command"] for g in settings["hooks"]["Stop"] for h in g["hooks"])
      and any(h["command"] == "echo mine" for g in settings["hooks"]["Stop"] for h in g["hooks"]))
check("iTerm2 defaults deleted (stubbed)", "delete com.googlecode.iterm2 ShowNewOutputIndicator" in open(log).read())

# ---- untouched bundled icon is removed on uninstall ------------------------------------------
home2 = tempfile.mkdtemp(prefix="inst-test-")
env2 = dict(os.environ, HOME=home2, PATH=stubs + os.pathsep + os.environ["PATH"])
subprocess.run(["bash", os.path.join(ROOT, "install.sh")], env=env2, capture_output=True, text=True)
subprocess.run(["bash", os.path.join(ROOT, "install.sh"), "--uninstall"], env=env2, capture_output=True, text=True)
check("untouched bundled icon removed on uninstall", not os.path.exists(os.path.join(home2, ".agent-tabs/icons/codex.png")))

print("failures: %d" % len(failures))
sys.exit(1 if failures else 0)
