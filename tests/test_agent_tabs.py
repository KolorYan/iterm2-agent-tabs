"""Test the aggregator script logic against a mocked iTerm2 API. Run: cd tests && python3 test_agent_tabs.py"""
import asyncio, os, sys, types, tempfile, importlib.util
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["HOME"]=tempfile.mkdtemp()
os.makedirs(os.path.expanduser("~/.agent-tabs/icons")); open(os.path.expanduser("~/.agent-tabs/icons/codex.png"),"wb").close()  # the custom icon is only set if the file exists
import iterm2 as it
P="\\(currentSession.user.agentState?)\\(currentSession.name)"
def S(i,st,t,grp=None):
    x=it.Session(i,st); x.vars["terminalIconName"]=t; x.vars["jobName"]="claude"
    if grp: x.vars["user.agentGroup"]=grp
    return x
RIME="/Users/me/Library/Rime\tLibrary/Rime ⎇ main"
s1=S("s1","🔵 ","✳ Refactor payment module"); s2=S("s2","🟡 ","⠸ Fix login | tool"); s3=S("s3",None,"zsh"); s4=S("s4","⚪ ","✳ Claude Code")
# t5: after the aggregator script restarts it meets a title it set itself last time (no record in memory); it should take over again instead of treating it as a user-defined title
s5=S("s5","🟢 ","✳ Deploy script"); t5=it.Tab("t5",[s5],"🔵 Deploy script")
# Grouping: t6/t8 share a directory (with t7 in between); they should get the group prefix and be moved together; t7 is a group of its own and gets no prefix
s6=S("s6","🔵 ","Edit config",RIME); s7=S("s7","🟢 ","Other project","/x/y	y/other")
s8=S("s8","⚪ ","Fix bug",RIME)
t6=it.Tab("t6",[s6],P); t7=it.Tab("t7",[s7],P); t8=it.Tab("t8",[s8],P)
# t9: codex session (fields 3 and 4 of the group info are tool and account); it should get the custom icon; Claude sessions keep the automatic icon
s9=S("s9","🟢 ","codex session","/x/z	z	codex	alice@example.com"); t9=it.Tab("t9",[s9],P)
t1=it.Tab("t1",[s1,s2],P); t2=it.Tab("t2",[s3],P); t3=it.Tab("t3",[s4],None)
for t in (t1,t2,t3,t5,t6,t7,t8,t9):
    for s in t.sessions: s.tab=t
win=it.Win([t1,t2,t3,t5,t6,t7,t8,t9]); it.APP=it.App([win])
spec=importlib.util.spec_from_file_location("a",os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent_tabs.py")); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
async def run():
    task=asyncio.create_task(it.MAIN(None)); await asyncio.sleep(0.3)
    print("t1:", t1.title, "| t2:", t2.title, "| t3:", t3.title, len(s1.injected))
    print("t5 retaken:", t5.title, "| tabVar:", t5.vars.get("user.agentTabFmt"))
    print("order after grouping:", [t.tab_id for t in win.tabs])
    print("codex session icon:", s9.profile.icon_mode, s9.profile.custom_icon_path.endswith("codex.png"), "| claude session icon mode:", s6.profile.icon_mode)
    print("t6:", t6.title, "| t7 (single, no prefix):", t7.title, "| t8:", t8.title)
    s2.vars["user.agentState"]="🔵 "; await it.CB[1](None,types.SimpleNamespace(identifier="s2")); await asyncio.sleep(0.2)
    print("t1 after approve:", t1.title, len(s1.injected))
    s1.vars["terminalIconName"]="✶ Refactor payment module"; await it.CB[2](None,types.SimpleNamespace(identifier="s1")); await asyncio.sleep(0.2)
    print("t1 spinner-only change:", t1.title, len(s1.injected))
    t1.vars["titleOverrideFormat"]="my tab"; s1.vars["user.agentState"]="🟢 "; s2.vars["user.agentState"]="🟢 "
    await it.CB[1](None,types.SimpleNamespace(identifier="s1")); await asyncio.sleep(0.2)
    print("t1 user renamed:", t1.vars["titleOverrideFormat"], s1.injected[-1][:25])
    s4.vars["user.agentState"]=""; await it.CB[1](None,types.SimpleNamespace(identifier="s4")); await asyncio.sleep(0.2)
    print("t3 end:", repr(t3.vars["titleOverrideFormat"]), m.agent_tabs.keys())
    print("main task exited abnormally:", task.done() and not task.cancelled() and repr(task.exception()))  # should be False
    task.cancel()
asyncio.run(run())
