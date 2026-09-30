"""测试用的 iterm2 模块替身（只实现 agent_tabs.py 用到的接口）"""
import asyncio, enum, types
class VariableScopes(enum.Enum):
    SESSION = 1
    APP = 4
class IconMode(enum.Enum):
    NONE = 0
    AUTOMATIC = 1
    CUSTOM = 2
class LocalWriteOnlyProfile:
    def __init__(s): s.props={}
    def set_icon_mode(s, v): s.props["icon_mode"]=v.value
    def set_custom_icon_path(s, v): s.props["custom_icon_path"]=v
class Session:
    def __init__(s, sid, state=None): s.session_id=sid; s.vars={"user.agentState": state}; s.injected=[]; s.tab=None; s.profile=types.SimpleNamespace(icon_mode=IconMode.AUTOMATIC.value, custom_icon_path="")
    async def async_get_variable(s, n): return s.vars.get(n)
    async def async_inject(s, b): s.injected.append(b)
    async def async_get_profile(s): return s.profile
    async def async_set_profile_properties(s, local):
        for k, v in local.props.items(): setattr(s.profile, k, v)
class Tab:
    def __init__(s, tid, sessions, fmt): s.tab_id=tid; s.sessions=sessions; s.vars={"titleOverrideFormat": fmt}; s.title=None; s.current_session=sessions[0]
    [setattr(x,'tab',s) for x in []]
    async def async_get_variable(s, n): return s.vars.get(n)
    async def async_set_variable(s, n, v): s.vars[n]=v
    async def async_set_title(s, t): s.title=t; s.vars["titleOverrideFormat"]=t
class Win:
    def __init__(s, tabs, wid="w1"): s.tabs=tabs; s.window_id=wid
    async def async_set_tabs(s, tabs):
        rest=[t for t in s.tabs if t not in tabs]
        s.tabs=list(tabs)+rest
class App:
    def __init__(s, wins): s.terminal_windows=wins
    async def async_get_variable(s, n): return getattr(s, "theme", "light")
    def get_tab_by_id(s, i): return next((t for w in s.terminal_windows for t in w.tabs if t.tab_id==i), None)
    def get_session_by_id(s, i): return next((x for w in s.terminal_windows for t in w.tabs for x in t.sessions if x.session_id==i), None)
APP=None; CB=[]
async def async_get_app(c): return APP
notifications = types.SimpleNamespace()
async def _sub(conn, cb, scope, name, ident): CB.append(cb)
notifications.async_subscribe_to_variable_change_notification=_sub
class _Mon:
    def __init__(s, c): pass
    async def __aenter__(s): return s
    async def __aexit__(s,*a): pass
    async def async_get(s): await asyncio.Event().wait()
class _FocusMon(_Mon):
    async def async_get_next_update(s): await asyncio.Event().wait()
LayoutChangeMonitor=_Mon; SessionTerminationMonitor=_Mon; FocusMonitor=_FocusMon
MAIN=None
def run_forever(main):
    global MAIN; MAIN=main
