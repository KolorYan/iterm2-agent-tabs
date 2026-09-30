#!/usr/bin/env python3
"""iTerm2 AutoLaunch script: aggregates the agent state of all panes in a tab onto the tab itself.

Each pane's state is written by agent-tab-state.sh into the session variable user.agentState;
this script picks the most urgent one (🔴 > 🟠 > 🔵 > 🟢 > ⚪):
- the tab title becomes "icon + that pane's session name", with the agent's own animated symbols stripped;
- all panes in the tab get the same tab color (iTerm2 uses the color of the currently selected pane for the tab).
"""
import asyncio
import ctypes
import ctypes.util
import functools
import logging
import logging.handlers
import os
import re
import subprocess
import time

import iterm2

# 🟡 waiting for your confirmation (blocking, most urgent) > 🔴 error > 🔵 running > 🔶 background work pending > 🟢 done > ⚪ idle
# 🟠 is the legacy "error" icon, kept so leftover state on old tabs is still recognized
# 🔶 = the model has finished its turn, but a shell started via run_in_background has not exited yet. Ranked below 🔵:
# when one tab has a pane doing real work and another pane with only background tasks left, the former deserves your attention first
PRIORITY = {"🟡": 6, "🔴": 5, "🟠": 5, "🔵": 4, "🔶": 3, "🟢": 2, "⚪": 1}
ICONS = ("🟡", "🔴", "🟠", "🔵", "🔶", "🟢", "⚪")
# Background colors are only applied to the two states that "need your hands" (🟡 awaiting confirmation, 🔴 error); 🔵 running / 🟢 done / ⚪ idle
# get no color at all -- a rainbow of colored tabs is the main source of "tackiness"; leaving them blank lets the one that matters stand out.
# The state is still distinguished by the small dot before the title, so no information is lost.
# Why these hues: on a dark background a "low-lightness yellow" inevitably reads as brown/olive (tried four levels, all looked dingy), so the attention color goes to purple --
# purple stays clean at low lightness and won't be confused with the "cyan for running" or the "crimson for error". The three hues don't clash on a pure black background.
# Only states that "need your hands" get a state color; the background for running/done/idle is left to the group color (see GROUP_PALETTE)
# 🔶 background-work-pending gets no color either: it doesn't need your hands, so the background goes to the group color as usual. I tried giving it a warm orange,
# but on a dark background that brown lands right in the "low-lightness yellow reads as brown/olive" trap above and looks dingy at a glance -- the ⋯ glyph is recognizable enough
PALETTES = {
    "light": {"🟡": (232, 224, 245), "🔴": (247, 221, 226), "🟠": (254, 214, 202)},
    "dark": {"🟡": (46, 31, 74), "🔴": (58, 21, 32), "🟠": (100, 56, 41)},
}
# The marker before the title uses monochrome text glyphs (same visual family as the spinner), not emoji dots --
# emoji have highlights and gradients and look dated; color semantics are already carried by the tab background.
# Error uses a solid triangle rather than ✕: ✕ looks too much like a "close button" and its stroke style doesn't match ●;
# ▲ and ● are both solid geometric shapes with the same stroke weight, and ▲ is still the most universal "warning" shape.
# Background-work-pending uses ⋯: three dots mean "not finished", the opposite of ✓'s "closure", so they're easy to tell apart.
# The braille-dot spinner is deliberately not used here because this glyph must be static -- when a background task ends there may be no hook event to refresh it,
# a leftover static glyph just means "info is stale", whereas a leftover endlessly spinning one makes people think it is really still running
GLYPHS = {"🟡": "●", "🔴": "▲", "🟠": "▲", "🔶": "⋯", "🟢": "✓", "⚪": ""}
# Claude Code also writes its state into the terminal title: ✳ = idle, waiting for your input; ◐◓◑◒ = working.
# This marker still updates when the user interrupts with ESC, whereas hooks don't -- Claude Code has no Interrupt event at all
# (every "Interrupt" in the binary is a C-runtime errno string), and an interrupt doesn't fire Stop either.
# So agentState stays on 🔵 forever and that tab keeps spinning in the sidebar until you next type in it.
# Hence the title is used as a fallback signal to clean up such leftovers. Only the ✳ marker is trusted; no other symbol is guessed at.
IDLE_MARK = "✳"
STALE_AFTER = 30   # 🔵 must stay this long before we touch it, to avoid the small timing gap between hook and title updates
stale_seen = {}    # session_id -> (agentState last seen, time this value was first seen)
bg_seen = set()     # tab_id -> 🔶 already switched to and looked at; once seen it stops breathing until 🔶 is re-entered

# Tabs "awaiting confirmation" toggle between a darker and a lighter shade to create a breathing effect -- grab attention with motion
# rather than large blocks of saturated color (which looks dingy at a glance). Set BLINK_INTERVAL to 0 to disable blinking
BLINK_ICON = "🟡"
BLINK_INTERVAL = 0.8
BLINK_ALT = {"light": (214, 201, 238), "dark": (70, 48, 110)}
# The whole "background work pending" tab also breathes; the bright phase is cyan (low-lightness cyan on a dark background doesn't read as
# brown/olive the way yellow/orange do, so it's not the same pitfall as 🟠 above and needn't be avoided); the dark phase is this tab's own group color
# (falling back to the default color if it has no group), so grouping info isn't lost while breathing. The interval is one notch slower than 🟡: 🔶 already
# ranks below 🟡 (see PRIORITY), so its animation should be quieter too and must not steal 🟡's attention
BG_BLINK_INTERVAL = 1.4
BG_BLINK_ALT = {"light": (196, 224, 224), "dark": (24, 58, 62)}
# Titles of "running" tabs get a spinner in front so you can tell at a glance the agent is still working. Set SPIN_INTERVAL to 0 to fall back to a static 🔵
SPIN_ICON = "🔵"
BG_ICON = "🔶"     # model has stopped but a background shell is still running
DONE_ICON = "🟢"
SPIN_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPIN_INTERVAL = 0.14
spin = {"i": 0}
THEME = {"mode": "light", "minimal": False}
bg_dark_cache = {}  # session_id -> whether the terminal background is dark (under the Minimal theme the tab bar uses the terminal background color)
# Default tab title in the AI Agents profile (used when the aggregator script isn't running)
PROFILE_TITLE = "\\(currentSession.user.agentState?)\\(currentSession.name)"
OLD_AGG_TITLE = "\\(user.agentTab?)\\(currentSession.name)"  # title left behind by older versions
HEARTBEAT = os.path.expanduser("~/.agent-tabs/aggregator.alive")
PIDFILE = os.path.expanduser("~/.agent-tabs/aggregator.pid")
LOG_FILE = os.path.expanduser("~/.agent-tabs/aggregator.log")

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
log = logging.getLogger("agent_tabs")
log.setLevel(logging.INFO)
_handler = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=200_000, backupCount=1, encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_handler)

# Animation and status symbols that Claude Code / Codex draw in the title; already expressed by the icon, so strip them
SPINNER_CHARS = set("✳✢✶✻✽✺✹✸✷·•*◐◓◑◒") | {chr(c) for c in range(0x2800, 0x2900)}

agent_tabs = {}  # tab_id -> {"icon": aggregated icon, "fmt": title set by this script or None, "orig": title before takeover}
# Sessions in the same working directory form a group (group info is written to user.agentGroup by agent-tab-state.sh,
# format "<dir>\t<group name>\t<AI tool>\t<account>"; the last two fields may be omitted and default to empty).
# Three dimensions: directory (first) -> AI tool claude/codex (second) -> login account (third).
# Sorting: tabs with group info are sorted as a whole by directory -> tool -> account -> tab title (four levels, case-insensitive);
# manually dragged tabs and tabs without group info don't participate and stay at their original index (see grouped_order / regroup).
# Within one directory, each (tool, account) subgroup gets a different group color.
# iTerm2 3.7 has native tab groups, but neither the Python API nor AppleScript exposes them, so grouping can only be expressed by
# "title prefix + moving tabs of the same group together".
# The group name is not put into the title: the sidebar is only so wide, and a group prefix would crowd out the session name that actually needs to be told apart.
# Grouping shows up only as "tabs of the same group are automatically placed together"; for the group name itself, look at the badge watermark in the top right.
GROUP_SEP = ""
tab_groups = {}    # tab_id -> (dir, group name, AI tool, account)
tab_window = {}    # tab_id -> window_id
group_counts = {}  # (window_id, dir) -> number of tabs in this window that belong to this group
group_slots = {}   # (window_id, dir, AI tool, account) -> index of the group color in GROUP_PALETTE
# Group colors: low saturation, lightness pushed close to the terminal background; only used to express "these tabs belong together".
# Avoid purple (awaiting confirmation) and red (error), the two state colors, to prevent confusion.
# Two colors alternate (zebra stripes): group colors only need to show that "two adjacent groups are different groups"; nobody needs to remember which color is which project,
# so two are enough. Both are cool colors with lightness close to the background, avoiding purple (awaiting confirmation) and red (error), the two state colors.
GROUP_PALETTE = {
    "light": [(228, 239, 241), (230, 233, 244)],
    "dark": [(20, 40, 46), (30, 35, 64)],
}


def osc(body):
    return ("\033]" + body + "\007").encode()


def rgb_seq(rgb):
    r, g, b = rgb
    return (osc("6;1;bg;red;brightness;%d" % r)
            + osc("6;1;bg;green;brightness;%d" % g)
            + osc("6;1;bg;blue;brightness;%d" % b))


def color_seq(icon, mode=None):
    rgb = PALETTES[mode or THEME["mode"]].get(icon)
    if not rgb:
        return osc("6;1;bg;*;default")
    return rgb_seq(rgb)


async def inject_all(tab, seq):
    for s in tab.sessions:
        try:
            await s.async_inject(seq)
        except Exception:
            pass


def clean_title(title):
    words = [w for w in re.split(r"\s+", (title or "").strip())
             if w and not all(ch in SPINNER_CHARS for ch in w)]
    text = " ".join(words)
    text = re.sub(r"\[ ?! ?\] ?Action Required", "", text)
    text = re.sub(r"\s*\|\s*", " · ", text)
    return text.strip(" ·|:-")


def literal(text):
    # the tab title is an iTerm2 interpolated string, so backslashes must be escaped
    return text.replace("\\", "\\\\")


def claim_pidfile():
    """Record our own pid; an instance started later overwrites it, and the earlier one notices via owns_pidfile() and exits."""
    os.makedirs(os.path.dirname(PIDFILE), exist_ok=True)
    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))


def owns_pidfile():
    try:
        with open(PIDFILE) as f:
            return f.read().strip() == str(os.getpid())
    except Exception:
        return True  # if the file was deleted, assume we are still the only instance


def group_prefix(tab_id):
    """Show the group prefix only when at least two tabs in the same window belong to the same group -- one alone isn't a group."""
    g = tab_groups.get(tab_id)
    if not g:
        return ""
    if not GROUP_SEP or group_counts.get((tab_window.get(tab_id), g[0]), 0) < 2:
        return ""
    return g[1] + GROUP_SEP


def group_color(tab_id, mode):
    """Quiet states (running/done/idle) use the group color; a lone session isn't a group and gets no color."""
    g = tab_groups.get(tab_id)
    if not g:
        return None
    ck = (tab_window.get(tab_id), g[0])
    if group_counts.get(ck, 0) < 2:
        return None
    pal = GROUP_PALETTE[mode]
    return pal[group_slots.get(ck + (g[2], g[3]), 0) % len(pal)]


# Hanzi in sort keys are sorted by pinyin: call macOS's built-in CoreFoundation directly (CFStringTransform hanzi -> Latin letters + strip tone marks),
# no third-party libs, so nothing is lost when iTerm2 upgrades and rebuilds its Python environment. Polyphonic characters may be misread (e.g. the word for "bank"
# is read as "yin xing"), which is fine for common titles; if the call fails it falls back to lowercasing only.
try:
    _cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
    _cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    _cf.CFStringCreateWithCString.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32)
    _cf.CFStringCreateMutableCopy.restype = ctypes.c_void_p
    _cf.CFStringCreateMutableCopy.argtypes = (ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p)
    _cf.CFStringTransform.restype = ctypes.c_ubyte
    _cf.CFStringTransform.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ubyte)
    _cf.CFStringGetLength.restype = ctypes.c_long
    _cf.CFStringGetLength.argtypes = (ctypes.c_void_p,)
    _cf.CFStringGetMaximumSizeForEncoding.restype = ctypes.c_long
    _cf.CFStringGetMaximumSizeForEncoding.argtypes = (ctypes.c_long, ctypes.c_uint32)
    _cf.CFStringGetCString.restype = ctypes.c_ubyte
    _cf.CFStringGetCString.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32)
    _cf.CFRelease.argtypes = (ctypes.c_void_p,)
    _CF_UTF8 = 0x08000100
    _CF_MANDARIN_LATIN = ctypes.c_void_p.in_dll(_cf, "kCFStringTransformMandarinLatin").value
    _CF_STRIP_DIACRITICS = ctypes.c_void_p.in_dll(_cf, "kCFStringTransformStripDiacritics").value
except Exception:
    _cf = None


@functools.lru_cache(maxsize=4096)
def sort_key(text):
    """Sort key: hanzi converted to pinyin (tones and diacritics stripped), all casefolded, so Chinese and English mix alphabetically, case-insensitively."""
    text = text or ""
    if _cf is None or text.isascii():
        return text.casefold()
    src = mut = None
    try:
        src = _cf.CFStringCreateWithCString(None, text.encode("utf-8"), _CF_UTF8)
        mut = _cf.CFStringCreateMutableCopy(None, 0, src)
        _cf.CFStringTransform(mut, None, _CF_MANDARIN_LATIN, 0)
        _cf.CFStringTransform(mut, None, _CF_STRIP_DIACRITICS, 0)
        n = _cf.CFStringGetMaximumSizeForEncoding(_cf.CFStringGetLength(mut), _CF_UTF8) + 1
        buf = ctypes.create_string_buffer(n)
        if _cf.CFStringGetCString(mut, buf, n, _CF_UTF8):
            return buf.value.decode("utf-8").casefold()
    except Exception:
        pass
    finally:
        for r in (src, mut):
            if r:
                _cf.CFRelease(r)
    return text.casefold()


# Title snapshot used for sorting: titles get changed by Claude itself, and using the live title would make tabs jump around in the sidebar.
# So each tab's sort title is only refreshed when it first appears and when you switch to it ("switch away and back").
sort_names = {}  # tab_id -> title snapshot used for sorting


def current_name(tab_id):
    return (agent_tabs.get(tab_id) or {}).get("name") or ""


def grouped_order(window_id, tabs, pinned=()):
    """Tabs with group info are sorted as a whole by directory -> AI tool -> account -> tab title (four levels, case-insensitive, hanzi by pinyin);
    ties on all four levels keep their existing relative order (stable sort), so tabs in the same directory naturally end up together.
    The title comes from the snapshot in sort_names (plain title, no status icon or spinner glyph) and is only updated when you switch to that tab.
    Tabs that don't take part in sorting and stay at their current index come in two kinds:
    - pinned: tabs that were dragged manually;
    - tabs without group info (plain terminals with no agent, new tabs whose agent hasn't sent its first hook yet).
    The remaining tabs fill the remaining slots in sorted order. window_id is kept for caller compatibility."""
    anchored = {i for i, t in enumerate(tabs) if t.tab_id in pinned or tab_groups.get(t.tab_id) is None}
    free = sorted((t for i, t in enumerate(tabs) if i not in anchored),
                  key=lambda t: (tuple(sort_key(p) for p in tab_groups[t.tab_id][0].split("/")),  # compare path segment by segment so parent and child dirs stay adjacent
                                 sort_key(tab_groups[t.tab_id][2]),
                                 sort_key(tab_groups[t.tab_id][3]),
                                 sort_key(sort_names.get(t.tab_id, ""))))
    it = iter(free)
    return [tabs[i] if i in anchored else next(it) for i in range(len(tabs))]


async def scan_groups(app):
    tab_groups.clear()
    tab_window.clear()
    group_counts.clear()
    for w in app.terminal_windows:
        for t in w.tabs:
            tab_window[t.tab_id] = w.window_id
            for sess in t.sessions:
                raw = (await get_var(sess, "user.agentGroup")) or ""
                if "\t" in raw:
                    key, label, *rest = raw.split("\t")
                    rest += [""] * (2 - len(rest))
                    tab_groups[t.tab_id] = (key, label, rest[0], rest[1])
                    ck = (w.window_id, key)
                    group_counts[ck] = group_counts.get(ck, 0) + 1
                    break
    # Assign group color indices in the order tabs appear in the sidebar, so two adjacent groups always get different colors (zebra stripes).
    # They can't be assigned in dictionary order -- two adjacent groups might then get the same color and the alternation would break.
    group_slots.clear()
    for w in app.terminal_windows:
        n = 0
        for t in w.tabs:
            g = tab_groups.get(t.tab_id)
            if not g:
                continue
            ck = (w.window_id, g[0], g[2], g[3])
            if group_counts.get(ck[:2], 0) < 2 or ck in group_slots:
                continue
            group_slots[ck] = n
            n += 1


# Session icons: Claude relies on iTerm2's automatic icon (Icon in the AI Agents profile set to Automatic, matched by process title claude);
# for tools iTerm2 has no built-in icon for, a custom icon is set per session here: put the image at ICON_DIR/<tool>.png; if the file is missing, nothing is set.
# This is a session-level property override and detaches the session from the profile (later profile-file changes no longer apply to it),
# so only tools in CUSTOM_ICON_TOOLS are touched, and only written when the tool changes. When the session's tool is no longer that one, revert to Automatic (i.e. the profile's mode).
ICON_DIR = os.path.expanduser("~/.agent-tabs/icons")
CUSTOM_ICON_TOOLS = {"codex"}
session_icons = {}  # session_id -> custom icon path already applied
icons_seeded = False


async def sync_session_icons(app):
    global icons_seeded
    live = set()
    for w in app.terminal_windows:
        for t in w.tabs:
            for sess in t.sessions:
                live.add(sess.session_id)
                try:
                    if not icons_seeded:
                        # after a script restart there is no record in memory: first read the session's current icon, so custom icons set earlier don't linger
                        prof = await sess.async_get_profile()
                        cur = prof.custom_icon_path or ""
                        if int(prof.icon_mode or 0) == iterm2.IconMode.CUSTOM.value and cur.startswith(ICON_DIR):
                            session_icons[sess.session_id] = cur
                    raw = (await get_var(sess, "user.agentGroup")) or ""
                    parts = raw.split("\t")
                    tool = parts[2] if len(parts) > 2 else ""
                    want = None
                    if tool in CUSTOM_ICON_TOOLS:
                        path = os.path.join(ICON_DIR, tool + ".png")
                        if os.path.isfile(path):
                            want = path
                    if want == session_icons.get(sess.session_id):
                        continue
                    local = iterm2.LocalWriteOnlyProfile()
                    if want:
                        local.set_icon_mode(iterm2.IconMode.CUSTOM)
                        local.set_custom_icon_path(want)
                    else:
                        local.set_icon_mode(iterm2.IconMode.AUTOMATIC)
                    await sess.async_set_profile_properties(local)
                    if want:
                        session_icons[sess.session_id] = want
                    else:
                        session_icons.pop(sess.session_id, None)
                    log.info("session %s icon -> %s", sess.session_id, want or "automatic")
                except Exception:
                    log.exception("session icon sync failed: %s", sess.session_id)
    icons_seeded = True
    for sid in list(session_icons):
        if sid not in live:
            session_icons.pop(sid, None)


# Detecting manual drags: after each pass the script records the window's tab order as a baseline; if on the next pass the tabs' relative order
# doesn't match the baseline, someone dragged -- the tabs outside the longest common subsequence are the ones that were dragged.
# Dragged tabs get flagged (signature = dir/tool/account, also written to a tab variable so it's still recognized after a script restart),
# and no longer take part in gathering and sorting; if their directory, tool or account changes (signature mismatch) the flag is cleared automatically.
window_order = {}    # window_id -> tab order last confirmed by the script
manual_pins = {}     # tab_id -> signature at the time of the drag
pins_loaded = set()  # tabs whose flag has already been read from tab variables


def group_sig(tab_id):
    g = tab_groups.get(tab_id)
    return "\t".join((g[0], g[2], g[3])) if g else None


def lcs_ids(a, b):
    """Set of elements in the longest common subsequence of a and b. There are very few tabs, so O(n*m) is enough."""
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if a[i] == b[j] else max(dp[i + 1][j], dp[i][j + 1])
    keep, i, j = set(), 0, 0
    while i < n and j < m:
        if a[i] == b[j]:
            keep.add(a[i])
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return keep


async def regroup(app):
    # An old instance taken over by a new one (it only exits after up to 20 seconds of heartbeat) must not touch tab order any more: two instances each sorting their own way,
    # and the new one would misread the old one's rearranging as "user dragged manually" and write the misjudged flag into tab variables
    if not owns_pidfile():
        return
    live_tabs, live_windows = set(), set()
    for w in app.terminal_windows:
        tabs = list(w.tabs)
        cur = [t.tab_id for t in tabs]
        live_windows.add(w.window_id)
        live_tabs.update(cur)
        for tid in cur:
            if not sort_names.get(tid):  # take a snapshot only on first sight (or if there was no title before); afterwards only update when switching to that tab
                sort_names[tid] = current_name(tid)
        for t in tabs:
            if t.tab_id not in pins_loaded:
                pins_loaded.add(t.tab_id)
                saved = await get_var(t, "user.agentPinned")
                if saved:
                    manual_pins[t.tab_id] = saved
        base = window_order.get(w.window_id)
        if base:
            cur_set, base_set = set(cur), set(base)
            base_f = [i for i in base if i in cur_set]   # compare only tabs present on both sides; newly opened or closed tabs don't count as drags
            cur_f = [i for i in cur if i in base_set]
            if base_f != cur_f:
                for tid in set(cur_f) - lcs_ids(base_f, cur_f):
                    sig = group_sig(tid)
                    if not sig:
                        continue
                    manual_pins[tid] = sig
                    tab = app.get_tab_by_id(tid)
                    if tab:
                        try:
                            await tab.async_set_variable("user.agentPinned", sig)
                        except Exception:
                            pass
                    log.info("tab %s was manually dragged, no longer auto-sorted", tid)
        pinned = {tid for tid in cur if group_sig(tid) and manual_pins.get(tid) == group_sig(tid)}
        order = grouped_order(w.window_id, tabs, pinned)
        order_ids = [t.tab_id for t in order]
        if order_ids != cur:
            try:
                await w.async_set_tabs(order)
                log.info("window %s: tabs reordered alphabetically by directory/tool/account", w.window_id)
            except Exception:
                log.exception("regroup failed: %s", w.window_id)
                order_ids = cur
        window_order[w.window_id] = order_ids
    for tid in list(manual_pins):
        if tid not in live_tabs:
            manual_pins.pop(tid, None)
    for tid in list(sort_names):
        if tid not in live_tabs:
            sort_names.pop(tid, None)
    pins_loaded.intersection_update(live_tabs)
    for wid in list(window_order):
        if wid not in live_windows:
            window_order.pop(wid, None)


async def set_tab_fmt(tab, value):
    # stored in the tab's own variable so that, after the aggregator script restarts, it can still tell which titles it set itself
    try:
        await tab.async_set_variable("user.agentTabFmt", value)
    except Exception:
        pass


async def get_var(obj, name):
    try:
        return await obj.async_get_variable(name)
    except Exception:
        return None


async def terminal_is_dark(session):
    sid = session.session_id
    if sid in bg_dark_cache:
        return bg_dark_cache[sid]
    sys_dark = THEME["mode"] == "dark"
    result = sys_dark
    try:
        profile = await session.async_get_profile()
        keys = ["Background Color"]
        if profile.use_separate_colors_for_light_and_dark_mode:
            keys.insert(0, "Background Color (Dark)" if sys_dark else "Background Color (Light)")
        for key in keys:
            c = profile.get_color_with_key(key)
            if c is not None:
                result = (0.299 * c.red + 0.587 * c.green + 0.114 * c.blue) / 255 < 0.5
                break
    except Exception:
        pass
    bg_dark_cache[sid] = result
    return result


async def palette_mode(tab):
    """Under the Minimal theme the sidebar colors follow the terminal background; under other themes they follow the system light/dark mode."""
    if THEME["minimal"] and tab.current_session:
        return "dark" if await terminal_is_dark(tab.current_session) else "light"
    return THEME["mode"]


async def is_ours(tab, fmt, prev):
    """Whether this tab's title can be taken over by this script (titles the user renamed manually are left alone)."""
    if fmt in (PROFILE_TITLE, OLD_AGG_TITLE, None, ""):
        return True
    if prev and fmt == prev.get("fmt"):
        return True
    # prev is lost after the aggregator script restarts or iTerm2 restores sessions; recognize titles we set ourselves via the tab variable and icon prefix,
    # otherwise those tabs would be treated as "user-customized titles" and never taken over again
    if fmt and fmt == await get_var(tab, "user.agentTabFmt"):
        return True
    marks = ICONS + tuple(SPIN_FRAMES) + tuple(g for g in GLYPHS.values() if g)
    return bool(fmt) and any(fmt.endswith(" " + m) or fmt.startswith(m + " ") for m in marks)


async def refresh_tab(tab, force_color):
    entries = []
    for s in tab.sessions:
        st = ((await get_var(s, "user.agentState")) or "").strip()
        entries.append((PRIORITY.get(st, 0), s, st))
    top = max((e[0] for e in entries), default=0)
    prev = agent_tabs.get(tab.tab_id)

    fmt = await get_var(tab, "titleOverrideFormat")
    if top == 0 and prev is None:
        # not an agent tab: only clean up when the title is clearly a leftover from this script; leave everything else alone
        if fmt and fmt not in (PROFILE_TITLE, OLD_AGG_TITLE) and await is_ours(tab, fmt, None):
            await tab.async_set_title(PROFILE_TITLE)
            await set_tab_fmt(tab, PROFILE_TITLE)
            log.info("tab %s: cleared stale title %r", tab.tab_id, fmt)
        return

    if top == 0:
        # all agents have exited: restore the default title and color
        if await is_ours(tab, fmt, prev):
            orig = prev.get("orig") or ""
            restored = PROFILE_TITLE if orig in ("", OLD_AGG_TITLE) else orig
            await tab.async_set_title(restored)
            await set_tab_fmt(tab, restored)
        await inject_all(tab, color_seq("", "light"))
        agent_tabs.pop(tab.tab_id, None)
        bg_seen.discard(tab.tab_id)
        log.info("tab %s: agent ended, restored", tab.tab_id)
        return

    # among equally urgent panes, prefer the current one
    candidates = [e for e in entries if e[0] == top]
    current = tab.current_session
    pick = next((e for e in candidates if current and e[1].session_id == current.session_id), candidates[0])
    icon, session = pick[2], pick[1]

    raw = (await get_var(session, "terminalIconName")) or (await get_var(session, "name")) or ""
    name = clean_title(raw) or ((await get_var(session, "jobName")) or "")
    name = group_prefix(tab.tab_id) + name
    # The marker goes at the start so the markers of all tabs line up in the same column. This requires turning off iTerm2's "smart truncation"
    # (TabTitlesUseSmartTruncation=false in install.sh): when several tabs share a prefix it switches to truncating from the head,
    # which would eat the leading marker first. With it off, truncation is always from the tail and the leading marker always survives.
    if icon == SPIN_ICON and SPIN_INTERVAL:
        label = "%s %s" % (SPIN_FRAMES[spin["i"] % len(SPIN_FRAMES)], name)
    else:
        glyph = GLYPHS.get(icon, icon)
        # Same principle as the state colors, "emphasize only the states that need your hands": these states already have a background color,
        # so the session name is also bolded -- color + weight double emphasis; other states stay as they are and don't get bold along --
        # bolding everything is the same as bolding nothing; only using it sparingly on a few states is real emphasis.
        # HTMLTabTitles only recognizes the three tags <b><i><u> (span/color/br have no effect -- tried that),
        # the bold wraps name itself and leaves the leading glyph alone, so is_ours()'s startswith(glyph+" ") can still recognize titles we set
        display_name = "<b>%s</b>" % name if icon in PALETTES["light"] and name else name
        label = "%s %s" % (glyph, display_name) if glyph else display_name

    managed_fmt = None
    if await is_ours(tab, fmt, prev):
        managed_fmt = literal(label)
        if managed_fmt != fmt:
            await tab.async_set_title(managed_fmt)
            await set_tab_fmt(tab, managed_fmt)

    mode = await palette_mode(tab)
    # "Awaiting confirmation / error" always use the state color over the group color -- what should grab attention mustn't be drowned out by the group color;
    # other quiet states use the group color, same-group backgrounds match, and visually they form a group
    rgb = PALETTES[mode].get(icon) or group_color(tab.tab_id, mode)
    if force_color or prev is None or prev.get("rgb") != rgb or prev.get("mode") != mode:
        await inject_all(tab, rgb_seq(rgb) if rgb else color_seq("", mode))

    if not prev or prev.get("icon") != icon or prev.get("fmt") != managed_fmt:
        log.info("tab %s: icon=%s title=%r managed=%s (fmt was %r) mode=%s",
                 tab.tab_id, icon, label, managed_fmt is not None, fmt, mode)
    if icon != BG_ICON:
        # left the "background work pending" state: the next entry counts as a new one and breathes again to remind
        bg_seen.discard(tab.tab_id)
    orig = prev.get("orig") if prev else fmt
    agent_tabs[tab.tab_id] = {"icon": icon, "fmt": managed_fmt, "orig": orig, "mode": mode,
                              "name": name, "rgb": rgb}


async def ps_children():
    """One ps call yields ppid -> [command lines], avoiding spawning a process per session repeatedly. Returns None on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ps", "-ax", "-o", "ppid=,command=",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
    except Exception:
        return None
    kids = {}
    for line in out.decode("utf-8", "replace").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            kids.setdefault(parts[0], []).append(parts[1])
    return kids


async def reconcile_stale(app):
    """Fallback correction for 🔵 leftovers after an ESC interrupt (see the comment at IDLE_MARK).

    Act only if all three conditions hold, to minimize false positives:
        1) agentState is 🔵 and has stayed at that value for more than STALE_AFTER seconds
        2) Claude Code itself marked ✳ in the title (it considers itself idle)
        3) probe whether the session still has background tasks -- if so, resolve to 🔶 rather than 🟢,
              because ESC only interrupts the model; it doesn't kill the shell started with run_in_background
    """
    kids = await ps_children()
    if kids is None:
        return 0   # probe failed: don't touch anything, wait for the next round
    now = time.time()
    changed = 0
    alive = set()
    for w in app.terminal_windows:
        for t in w.tabs:
            for s in t.sessions:
                sid = s.session_id
                alive.add(sid)
                st = ((await get_var(s, "user.agentState")) or "").strip()
                prev = stale_seen.get(sid)
                if not prev or prev[0] != st:
                    stale_seen[sid] = (st, now)   # state just changed, restart the timer
                    continue
                if st != SPIN_ICON or now - prev[1] < STALE_AFTER:
                    continue
                if not ((await get_var(s, "name")) or "").lstrip().startswith(IDLE_MARK):
                    continue
                jp = await get_var(s, "jobPid")
                busy = bool(jp) and any("shell-snapshots" in c for c in kids.get(str(jp), []))
                new = BG_ICON if busy else DONE_ICON
                try:
                    await s.async_set_variable("user.agentState", new)
                except Exception:
                    continue
                stale_seen[sid] = (new, now)
                changed += 1
                log.info("session %s: 🔵 leftover for %.0fs and title already ✳, corrected to %s", sid[:8], now - prev[1], new)
    for sid in [k for k in stale_seen if k not in alive]:
        del stale_seen[sid]
    return changed


async def sync_tab_bar_font_size(app):
    """The tab bar font size isn't rendered by the same layer as the content font size, and iTerm2 has no native "follow" setting (install.sh
    used to hard-code 14pt). Second-best approach here: once on each iTerm2 launch, read the font size from the current session's Profile
    (Settings -> Profiles -> Text) and write it into CustomTabBarFontSize --
    so after changing the content font size you don't need to change the tab font size by hand, though it only takes effect on the next iTerm2 restart (this too
    is because the defaults cache is read at startup, same as the other defaults entries in install.sh).
    """
    session = None
    for w in app.terminal_windows:
        for t in w.tabs:
            if t.current_session:
                session = t.current_session
                break
        if session:
            break
    if not session:
        return
    try:
        profile = await session.async_get_profile()
        m = re.search(r"(\d+)\s*$", profile.normal_font or "")
        if not m:
            return
        size = float(m.group(1))
        current = subprocess.run(
            ["defaults", "read", "com.googlecode.iterm2", "CustomTabBarFontSize"],
            capture_output=True, text=True,
        ).stdout.strip()
        if current and abs(float(current) - size) < 0.01:
            return
        subprocess.run(
            ["defaults", "write", "com.googlecode.iterm2", "CustomTabBarFontSize", "-float", str(size)],
            check=True,
        )
        log.info("tab bar font size synced to %.0f (from profile normal_font=%r)", size, profile.normal_font)
    except Exception:
        log.exception("sync_tab_bar_font_size failed")


async def main(connection):
    app = await iterm2.async_get_app(connection)
    await sync_tab_bar_font_size(app)
    dirty_tabs = {}  # tab_id -> whether the color needs to be reset
    dirty_all = asyncio.Event()
    wake = asyncio.Event()

    def mark_all():
        dirty_all.set()
        wake.set()

    async def worker():
        while True:
            await wake.wait()
            await asyncio.sleep(0.05)  # coalesce consecutive triggers
            wake.clear()
            force_all = dirty_all.is_set()
            pending = {}
            if force_all:
                await scan_groups(app)
                await sync_session_icons(app)
                bg_dark_cache.clear()
                dirty_all.clear()
                dirty_tabs.clear()
                tabs = [t for w in app.terminal_windows for t in w.tabs]
            else:
                pending = dict(dirty_tabs)
                dirty_tabs.clear()
                tabs = [t for t in (app.get_tab_by_id(i) for i in pending) if t]
            live = {t.tab_id for w in app.terminal_windows for t in w.tabs}
            for tid in list(agent_tabs):
                if tid not in live:
                    agent_tabs.pop(tid, None)
                    bg_seen.discard(tid)
            for t in tabs:
                try:
                    await refresh_tab(t, force_all or pending.get(t.tab_id, False))
                except Exception:  # an error in one tab doesn't affect the others
                    log.exception("refresh failed: tab %s", t.tab_id)
            if force_all:
                await regroup(app)

    def make_handler(force):
        async def handler(_connection, message):
            session = app.get_session_by_id(message.identifier) if message.identifier else None
            tab = session.tab if session else None
            if tab:
                if force or tab.tab_id in agent_tabs:
                    dirty_tabs[tab.tab_id] = dirty_tabs.get(tab.tab_id, False) or force
                    wake.set()
            elif force:
                mark_all()
        return handler

    async def update_theme():
        try:
            theme = await app.async_get_variable("effectiveTheme") or ""
        except Exception:
            theme = ""
        parts = str(theme).split()
        mode = "dark" if "dark" in parts else "light"
        minimal = "minimal" in parts
        if mode != THEME["mode"] or minimal != THEME["minimal"]:
            THEME["mode"] = mode
            THEME["minimal"] = minimal
            bg_dark_cache.clear()
            mark_all()

    async def on_theme_change(_connection, _message):
        await update_theme()

    await update_theme()
    await iterm2.notifications.async_subscribe_to_variable_change_notification(
        connection, on_theme_change, iterm2.VariableScopes.APP.value, "effectiveTheme", None)

    # every hook firing changes user.agentSeq; when the agent changes the title, terminalIconName changes
    await iterm2.notifications.async_subscribe_to_variable_change_notification(
        connection, make_handler(True), iterm2.VariableScopes.SESSION.value, "user.agentSeq", "all")
    await iterm2.notifications.async_subscribe_to_variable_change_notification(
        connection, make_handler(False), iterm2.VariableScopes.SESSION.value, "terminalIconName", "all")

    # as soon as group info changes (new session arrives, directory/branch changed) recompute everything, otherwise grouping waits for the 60-second heartbeat
    async def on_group_change(_connection, _message):
        mark_all()

    await iterm2.notifications.async_subscribe_to_variable_change_notification(
        connection, on_group_change, iterm2.VariableScopes.SESSION.value, "user.agentGroup", "all")

    async def watch_layout():
        async with iterm2.LayoutChangeMonitor(connection) as mon:
            while True:
                await mon.async_get()
                mark_all()

    async def watch_focus():
        """Only when you switch to a tab is its sort title updated to the latest, and a tidy-up triggered ("tidy only after switching away and back").
        If the title hasn't changed, do nothing, to avoid pointless reordering."""
        async with iterm2.FocusMonitor(connection) as mon:
            while True:
                update = await mon.async_get_next_update()
                if update.selected_tab_changed:
                    tid = update.selected_tab_changed.tab_id
                    name = current_name(tid)
                    if name and sort_names.get(tid) != name:
                        sort_names[tid] = name
                        mark_all()

    async def watch_termination():
        async with iterm2.SessionTerminationMonitor(connection) as mon:
            while True:
                await mon.async_get()
                mark_all()

    async def blinker():
        """Tabs awaiting confirmation alternate between two shades. The tab you're currently looking at doesn't blink, so as not to distract."""
        if not BLINK_INTERVAL:
            return
        phase = False
        while True:
            await asyncio.sleep(BLINK_INTERVAL)
            phase = not phase
            try:
                current = app.current_terminal_window.current_tab.tab_id
            except Exception:
                current = None
            for tid, info in list(agent_tabs.items()):
                if info.get("icon") != BLINK_ICON or tid == current:
                    continue
                tab = app.get_tab_by_id(tid)
                if not tab:
                    continue
                mode = info.get("mode") or THEME["mode"]
                await inject_all(tab, rgb_seq(BLINK_ALT[mode] if phase else PALETTES[mode][BLINK_ICON]))

    async def bg_blinker():
        """The whole background of a tab with background work pending breathes between the group color and cyan. Once you switch to it and take a look it counts as noticed and
        stops breathing (back to the regular background) until the tab leaves and re-enters 🔶 -- otherwise it would start blinking again every time you switch away,
        making looking at it the same as not looking."""
        if not BG_BLINK_INTERVAL:
            return
        phase = False
        while True:
            await asyncio.sleep(BG_BLINK_INTERVAL)
            phase = not phase
            try:
                current = app.current_terminal_window.current_tab.tab_id
            except Exception:
                current = None
            for tid, info in list(agent_tabs.items()):
                if info.get("icon") != BG_ICON:
                    continue
                tab = app.get_tab_by_id(tid)
                if not tab:
                    continue
                mode = info.get("mode") or THEME["mode"]
                if tid == current:
                    bg_seen.add(tid)
                    base = group_color(tid, mode)
                    await inject_all(tab, rgb_seq(base) if base else color_seq("", mode))
                    continue
                if tid in bg_seen:
                    continue
                if phase:
                    await inject_all(tab, rgb_seq(BG_BLINK_ALT[mode]))
                else:
                    base = group_color(tid, mode)
                    await inject_all(tab, rgb_seq(base) if base else color_seq("", mode))

    async def spinner():
        """Spin in front of the titles of running tabs. Only the title changes, not the tab color, and no tab variable is written per frame (too expensive)."""
        if not SPIN_INTERVAL:
            return
        while True:
            await asyncio.sleep(SPIN_INTERVAL)
            spin["i"] += 1
            frame = SPIN_FRAMES[spin["i"] % len(SPIN_FRAMES)]
            for tid, info in list(agent_tabs.items()):
                if info.get("icon") != SPIN_ICON or not info.get("fmt") or not info.get("name"):
                    continue
                tab = app.get_tab_by_id(tid)
                if not tab:
                    continue
                # the title isn't the one left by the previous frame, meaning the user renamed it (or something else touched it); back off and leave it to the full refresh
                if (await get_var(tab, "titleOverrideFormat")) != info["fmt"]:
                    continue
                fmt = literal("%s %s" % (frame, info["name"]))
                try:
                    await tab.async_set_title(fmt)
                    info["fmt"] = fmt
                except Exception:
                    pass

    async def heartbeat():
        os.makedirs(os.path.dirname(HEARTBEAT), exist_ok=True)
        n = 0
        while True:
            with open(HEARTBEAT, "w") as f:
                f.write(str(time.time()))
            if not owns_pidfile():
                # when iTerm2 quits, the old instance is adopted by launchd and auto-reconnects; after the new instance starts, two are fighting over the title
                log.info("another aggregator took over, exiting (pid %d)", os.getpid())
                os._exit(0)
            # every heartbeat checks for ESC leftovers (redraw immediately if the state changed); the full redraw is still once a minute
            if await reconcile_stale(app) or n % 3 == 0:
                mark_all()
            n += 1
            await asyncio.sleep(20)

    claim_pidfile()
    log.info("started; pid=%d theme=%s", os.getpid(), THEME)
    mark_all()
    await asyncio.gather(worker(), watch_layout(), watch_focus(), watch_termination(), heartbeat(), blinker(), bg_blinker(), spinner())


iterm2.run_forever(main)
