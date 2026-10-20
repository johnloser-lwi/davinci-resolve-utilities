import ctypes
import json
import math
import os
import re
import shutil
import sys
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

# Tell Windows we handle our own DPI, so a 4K/high-DPI screen reports real
# pixels instead of a blurry bitmap-scaled window. Must run before Tk starts.
for _mod, _fn, _arg in (("shcore", "SetProcessDpiAwareness", 2),
                        ("shcore", "SetProcessDpiAwareness", 1),
                        ("user32", "SetProcessDPIAware", None)):
    try:
        _f = getattr(getattr(ctypes.windll, _mod), _fn)
        _f(_arg) if _arg is not None else _f()
        break
    except Exception:
        continue

# Fusion Clipboard — a clipboard manager for Fusion nodes.
#
# It never touches the comp. Copy nodes in Fusion with Ctrl+C, click Save, and
# whatever Fusion put on the system clipboard is written to a file verbatim.
# Click Copy on a saved preset and that text goes straight back on the
# clipboard, ready for Ctrl+V in Fusion.
#
# WHY NOT AUTOMATE THE PASTE
#   An earlier version used comp.CopySettings() / comp.Paste(). That round trip
#   silently drops things — most visibly the user controls on a Group, which
#   come back bare. Fusion's own Ctrl+C blob carries them, so the fix is to stop
#   re-serialising it and just move the exact bytes around. Being a dumber tool
#   makes it a more faithful one.
#
# Revising a template: select it and hit Update from Clipboard. Name, folder,
# notes and favorite stay put, only the contents change. The previous version is
# kept beside it as a .setting.bak.
#
# RUNS STANDALONE
#   Because it never talks to Resolve or Fusion, this is just a Python/tkinter
#   app. Resolve's Scripts menu runs the deployed copy; "Fusion Clipboard.cmd"
#   in the repo root runs this one directly. Same file either way - there is no
#   second copy to keep in step.
#
# Presets are plain .setting files (browsable in Explorer, and still valid for
# Fusion's own Paste Setting) with a small .json sidecar holding notes / node
# types / favorite flag.

PREFS_FILE = os.path.expandvars(r"%APPDATA%\fusion_clipboard_prefs.json")
# Presets live next to the prefs files, same as the other scripts. Set
# "library_path" in the prefs JSON to point somewhere else.
DEFAULT_LIBRARY = os.path.expandvars(r"%APPDATA%\FusionClipboard")
RECENT_MAX = 15

BG = "#1e1e1e"
PANEL = "#252525"
PANEL2 = "#2d2d2d"
FG = "#e0e0e0"
SUB = "#9a9a9a"
BORDER = "#3a3a3a"
BTN = "#383838"
BTN_HOVER = "#454545"
ACCENT = "#3d7fd6"
ACCENT_HOVER = "#4a90e8"
GOLD = "#e0b040"

FAV_NODE = "__favorites__"
RECENT_NODE = "__recent__"
ALL_NODE = "__all__"

SCALE_CHOICES = ["Auto", "100%", "125%", "150%", "175%", "200%", "250%"]

# Every pixel dimension and font size below is multiplied by SCALE, so the
# whole panel grows uniformly on high-DPI screens.
SCALE = 1.0


def S(n):
    """Scale a pixel dimension."""
    return max(1, int(round(n * SCALE)))


def FONT(size, weight=None):
    """Scale a font's point size."""
    pts = max(6, int(round(size * SCALE)))
    return ("Segoe UI", pts, weight) if weight else ("Segoe UI", pts)


def detect_scale(root):
    """Work out a sensible UI scale for the screen we're opening on."""
    try:
        dpi = root.winfo_fpixels("1i")
    except Exception:
        dpi = 96.0
    scale = dpi / 96.0
    if scale < 1.05:
        # A 4K panel left at 100% Windows scaling still reports 96 DPI, so the
        # UI would be physically tiny — fall back to judging by resolution.
        try:
            width = root.winfo_screenwidth()
        except Exception:
            width = 1920
        if width >= 3400:
            scale = 1.5
        elif width >= 2800:
            scale = 1.25
    return max(1.0, min(scale, 3.0))


def resolve_scale(prefs, root):
    setting = prefs.get("ui_scale", "Auto")
    if isinstance(setting, str) and setting.endswith("%"):
        try:
            return max(1.0, min(int(setting[:-1]) / 100.0, 3.0))
        except ValueError:
            pass
    elif isinstance(setting, (int, float)):
        return max(1.0, min(float(setting), 3.0))
    return detect_scale(root)


# --------------------------------------------------------------------------
# prefs / storage
# --------------------------------------------------------------------------

def load_prefs():
    if os.path.exists(PREFS_FILE):
        try:
            with open(PREFS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_prefs(prefs):
    try:
        with open(PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)
    except Exception as e:
        print(f"Could not save prefs: {e}")


def sanitize(name):
    cleaned = "".join(c for c in name if c not in '<>:"/\\|?*').strip()
    return cleaned or "Untitled"


def meta_path(setting_path):
    return os.path.splitext(setting_path)[0] + ".json"


def backup_path(setting_path):
    """Previous contents, kept by Update from Clipboard."""
    return setting_path + ".bak"


def companion_paths(setting_path):
    """The files that travel with a preset: its sidecar and its backup."""
    return [meta_path(setting_path), backup_path(setting_path)]


def read_meta(setting_path):
    path = meta_path(setting_path)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def write_meta(setting_path, meta):
    try:
        with open(meta_path(setting_path), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
    except Exception as e:
        print(f"Could not write sidecar: {e}")


# --------------------------------------------------------------------------
# Windows clipboard
#
# The Win32 API is used rather than Tk's clipboard_append, because Tk only
# OWNS the clipboard while the app is alive — close this panel and the content
# it put there evaporates before you get to Ctrl+V. SetClipboardData hands the
# data to the system, so it survives.
# --------------------------------------------------------------------------

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

# Handles and pointers are 64-bit; without these the return values get
# truncated to 32 bits and the calls fail in ways that look like "empty
# clipboard" rather than an error.
_user32.GetClipboardData.restype = ctypes.c_void_p
_user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
_user32.SetClipboardData.restype = ctypes.c_void_p
_kernel32.GlobalAlloc.restype = ctypes.c_void_p
_kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
_kernel32.GlobalFree.restype = ctypes.c_void_p


def _open_clipboard(attempts=10):
    """Another app can hold the clipboard open for a moment — retry briefly."""
    import time
    for _ in range(attempts):
        if _user32.OpenClipboard(0):
            return True
        time.sleep(0.05)
    return False


def get_clipboard_text():
    """Whatever text is on the clipboard. Returns (text, error)."""
    if not _open_clipboard():
        return None, "another application is holding the clipboard"
    try:
        handle = _user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None, "the clipboard has no text on it"
        ptr = _kernel32.GlobalLock(handle)
        if not ptr:
            return None, "could not read the clipboard data"
        try:
            return ctypes.c_wchar_p(ptr).value, None
        finally:
            _kernel32.GlobalUnlock(handle)
    except Exception as e:
        return None, str(e)
    finally:
        _user32.CloseClipboard()


def set_clipboard_text(text):
    """Put text on the clipboard for real. Returns (ok, error)."""
    buffer = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(buffer)
    handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
    if not handle:
        return False, "could not allocate clipboard memory"
    ptr = _kernel32.GlobalLock(handle)
    if not ptr:
        _kernel32.GlobalFree(handle)
        return False, "could not lock clipboard memory"
    ctypes.memmove(ptr, buffer, size)
    _kernel32.GlobalUnlock(handle)

    if not _open_clipboard():
        _kernel32.GlobalFree(handle)
        return False, "another application is holding the clipboard"
    try:
        _user32.EmptyClipboard()
        if not _user32.SetClipboardData(CF_UNICODETEXT, handle):
            # Ownership only transfers on success, so on failure the block is
            # still ours to release.
            _kernel32.GlobalFree(handle)
            return False, "the system refused the clipboard data"
        return True, None
    finally:
        _user32.CloseClipboard()


# --------------------------------------------------------------------------
# reading Fusion's clipboard blob
# --------------------------------------------------------------------------

def looks_like_fusion_nodes(text):
    head = (text or "").lstrip()[:400]
    return head.startswith("{") and "Tools" in head


def parse_tool_types(text):
    """Node types out of a Fusion clipboard blob, in order."""
    return [block["type"] for block in scan_blocks(text)]


# --------------------------------------------------------------------------
# the blob scanner
#
# A Fusion clipboard blob nests as
#     { Tools = ordered() { Name = ToolType { Inputs = { ... } } } }
# so the nodes are the entries at brace depth 2.
#
# This walks CHARACTERS, not lines, and records index ranges. Two reasons:
#
#   - Rewriting a node needs exact offsets. A line scanner has none, and a node's
#     inputs are often all on one line.
#   - Counting "{" minus "}" per line has no idea what a string is. Braces inside
#     a Text+'s StyledText balance out by luck; ONE unbalanced brace in a title
#     ("50% off {") shifts the depth for the rest of the file and the scanner
#     starts treating nested entries as nodes. Ignoring braces inside strings
#     makes both cases moot.
# --------------------------------------------------------------------------

BLOCK_HEADER = re.compile(r"([A-Za-z_]\w*)\s*=\s*([A-Za-z_][\w.]*)\s*$")
#                                                        ^^^^^^^ dots matter:
# without them "ofx.com.blackmagicdesign.resolvefx.DropShadow" and
# "Fuse.Duplicate" never match, and every plugin node goes silently missing.


def _skip_string(text, i):
    """Index just past the string literal that starts at text[i]."""
    quote = text[i]
    i += 1
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == quote:
            return i + 1
        i += 1
    return i                      # unterminated: treat the rest as string


def _matching_brace(text, open_index):
    """Index of the '}' closing the '{' at open_index, string-aware."""
    depth = 0
    i = open_index
    while i < len(text):
        c = text[i]
        if c in '"\'':
            i = _skip_string(text, i)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _find_inputs_span(text, block_open, block_close):
    """(start, end) of the body of this block's own `Inputs = { ... }`.

    Only the block's immediate children are considered, so an `Inputs` belonging
    to a nested tool inside a Group is never mistaken for this one.
    """
    depth = 0
    i = block_open
    while i < block_close:
        c = text[i]
        if c in '"\'':
            i = _skip_string(text, i)
            continue
        if c == "{":
            depth += 1
            if depth == 2:
                header = text[max(0, i - 80):i]
                match = re.search(r"Inputs\s*=\s*(?:ordered\(\)\s*)?$", header)
                if match:
                    close = _matching_brace(text, i)
                    if close != -1:
                        return i + 1, close
                close = _matching_brace(text, i)
                i = close if close != -1 else i
                depth -= 1
        elif c == "}":
            depth -= 1
        i += 1
    return None


def scan_blocks(text):
    """Every node at depth 2, with the index ranges needed to rewrite it.

    Each record: name, type, type_span, block_span (the braces), inputs_span.
    """
    text = text or ""
    blocks = []
    depth = 0
    i = 0
    while i < len(text):
        c = text[i]
        if c in '"\'':
            i = _skip_string(text, i)
            continue
        if c == "{":
            # Depth 2 = inside the outer brace and inside Tools, which is where
            # the nodes live. "Tools = ordered() {" never matches the header
            # pattern (the parens), so it falls through and just adds depth.
            if depth == 2:
                # Look back over the "Name = Type" that introduces this brace.
                header = text[max(0, i - 200):i]
                match = BLOCK_HEADER.search(header.rstrip())
                close = _matching_brace(text, i)
                if match and close != -1:
                    type_end = max(0, i - 200) + match.end(2)
                    blocks.append({
                        "name": match.group(1),
                        "type": match.group(2),
                        "type_span": (type_end - len(match.group(2)), type_end),
                        "block_span": (i, close + 1),
                        "inputs_span": _find_inputs_span(text, i, close),
                    })
                    i = close + 1          # skip the whole node
                    continue
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return blocks


def split_entries(text, start, end):
    """`key = value` pairs at the top level of a brace body.

    Returns [{key, key_span, value, value_span}]. Nested braces and strings are
    stepped over, so `Center = Input { Value = { 0.39, 0.34 } }` comes back as
    one entry rather than three.
    """
    entries = []
    i = start
    while i < end:
        c = text[i]
        if c in '"\'':
            i = _skip_string(text, i)
            continue
        if c.isspace() or c == ",":
            i += 1
            continue

        key_match = re.compile(r"([A-Za-z_]\w*)\s*=\s*").match(text, i, end)
        if not key_match:
            i += 1
            continue

        value_start = key_match.end()
        j = value_start
        depth = 0
        while j < end:
            ch = text[j]
            if ch in '"\'':
                j = _skip_string(text, j)
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                if depth == 0:
                    break
                depth -= 1
            elif ch == "," and depth == 0:
                break
            j += 1

        entries.append({
            "key": key_match.group(1),
            "key_span": (i, i + len(key_match.group(1))),
            "value": text[value_start:j].rstrip(),
            "value_span": (value_start, j),
        })
        i = j + 1
    return entries


# --------------------------------------------------------------------------
# ResolveFX -> native Fusion conversion
#
# ResolveFX ship with Resolve and do NOT exist in Fusion Studio, so a preset
# using them comes up with missing tools there. Third-party OFX and Fuses are
# deliberately NOT touched or reported: install them in Studio and they resolve
# fine, which is why this keys off one exact prefix instead of trying to tell
# "native" from "not" (a Fuse like KD_Recolor is indistinguishable from a native
# tool by its type token alone).
# --------------------------------------------------------------------------

RESOLVEFX_PREFIX = "ofx.com.blackmagicdesign.resolvefx."
RULES_FILE = os.path.expandvars(r"%APPDATA%\fusion_clipboard_rules.json")
PLACEHOLDER = re.compile(r"<<.*?>>")

# Shipped defaults. Anything in <<angle brackets>> is a value nobody has
# verified yet, and a rule containing one REFUSES to run - see rule_blocked().
# That is deliberate: guessing at an undocumented schema has gone wrong on this
# project three times, and a wrong input name here is undetectable from the text
# because Fusion silently accepts unknown inputs and pastes the node at default.
DEFAULT_RULES = {
    "schema_version": 1,
    "rules": [
        {
            "id": "dropshadow_to_shadow",
            "source": RESOLVEFX_PREFIX + "DropShadow",
            "target": "<<Shadow>>",
            "notes": "Run Learn Node on a native Shadow (every control moved off "
                     "its default) and replace every <<...>> from the report.",
            "image_input": {"from": "Source", "to": "<<Input>>"},
            "params": [
                {"from": ["shadowStrength"], "to": ["<<ShadowStrength>>"],
                 "transform": "linear", "mul": 1.0, "add": 0.0},
                {"from": ["shadowBlur"], "to": ["<<ShadowBlur>>"],
                 "transform": "linear", "mul": 1.0, "add": 0.0,
                 "notes": "ResolveFX blur units almost certainly differ - calibrate mul."},
                {"from": ["shadowAngle", "ShadowDistance"],
                 "to": ["<<ShadowOffsetX>>", "<<ShadowOffsetY>>"],
                 "transform": "polar_to_xy",
                 "angle_zero_deg": 0.0, "angle_sign": 1, "distance_mul": 1.0,
                 "origin": [0.0, 0.0], "emit": "two_scalars",
                 "notes": "Angle convention and distance normalisation UNVERIFIED - "
                          "measure by setting shadowAngle to 0 then 90 and watching."},
                {"from": ["shadowColorRed"], "to": ["<<ShadowColorRed>>"],
                 "transform": "passthrough"},
                {"from": ["shadowColorGreen"], "to": ["<<ShadowColorGreen>>"],
                 "transform": "passthrough"},
                {"from": ["shadowColorBlue"], "to": ["<<ShadowColorBlue>>"],
                 "transform": "passthrough"},
            ],
        },
        {
            "id": "resolvefx_blur_to_blur",
            "source": "<<" + RESOLVEFX_PREFIX + "SOMETHING>>",
            "target": "Blur",
            "notes": "The native side is known from your own Animations/Fade Rotate "
                     "Blur preset. The ResolveFX side is not - no ResolveFX blur "
                     "exists in your library, so Learn Node one first.",
            "image_input": {"from": "Source", "to": "Input"},
            "params": [
                {"from": ["<<blurAmount>>"], "to": ["XBlurSize"],
                 "transform": "linear", "mul": 1.0, "add": 0.0},
            ],
            "defaults": [{"to": "Filter", "literal": 'FuID { "Fast Gaussian" }'}],
        },
    ],
}


def resource_dir():
    """Where a file shipped beside this script lives.

    Three different answers, because this runs three ways: from Resolve's
    Scripts folder, from the repo via the .cmd, and from inside a PyInstaller
    bundle (where data is unpacked to _MEIPASS, not next to the .exe). __file__
    is also absent in some embedded interpreters, hence the fallback.
    """
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return bundled
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.getcwd()


def load_rules():
    """Shipped defaults, overlaid by a repo file, overlaid by a user file.

    Later sources win per rule id. A broken file is reported rather than
    crashing the panel - the verbatim copy path must keep working regardless.
    """
    merged = {r["id"]: r for r in DEFAULT_RULES["rules"]}
    problems = []
    for path in (os.path.join(resource_dir(), "studio_safe_rules.json"),
                 RULES_FILE):
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for rule in data.get("rules", []):
                if rule.get("id"):
                    merged[rule["id"]] = rule
        except Exception as e:
            problems.append(f"{os.path.basename(path)}: {e}")
    return list(merged.values()), problems


def rule_blocked(rule):
    """The unverified bits of a rule, as a list of strings. Empty means usable."""
    found = []

    def walk(node, path):
        if isinstance(node, str):
            if PLACEHOLDER.search(node):
                found.append(f"{path} = {node}")
        elif isinstance(node, dict):
            for k, v in node.items():
                if k != "notes":
                    walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for n, v in enumerate(node):
                walk(v, f"{path}[{n}]")

    walk(rule, "")
    return found


def _number(value_text):
    """The number out of an `Input { Value = 0.55 }` body, or None."""
    match = re.search(r"\bValue\s*=\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)", value_text or "")
    return float(match.group(1)) if match else None


def _is_connected(value_text):
    return "SourceOp" in (value_text or "")


def _fmt(value):
    """A number as Fusion writes them - no scientific notation, no trailing junk."""
    text = f"{float(value):.10g}"
    return text


def apply_transform(spec, values):
    """Source numbers -> [(target_name, literal_text)]. Raises ValueError if the
    mapping cannot be made honestly."""
    kind = spec.get("transform", "passthrough")
    targets = spec.get("to", [])

    if kind in ("passthrough", "linear"):
        if values[0] is None:
            raise ValueError("source value missing")
        out = values[0] * float(spec.get("mul", 1.0)) + float(spec.get("add", 0.0))
        low, high = (spec.get("clamp") or [None, None])
        if low is not None:
            out = max(out, low)
        if high is not None:
            out = min(out, high)
        return [(targets[0], _fmt(out))]

    if kind == "polar_to_xy":
        angle, distance = values[0], values[1]
        if angle is None or distance is None:
            raise ValueError("angle or distance missing")
        theta = math.radians(angle * float(spec.get("angle_sign", 1))
                             + float(spec.get("angle_zero_deg", 0.0)))
        reach = distance * float(spec.get("distance_mul", 1.0))
        origin = spec.get("origin") or [0.0, 0.0]
        x = origin[0] + reach * math.cos(theta)
        y = origin[1] + reach * math.sin(theta)
        if spec.get("emit") == "point":
            return [(targets[0], "{ %s, %s }" % (_fmt(x), _fmt(y)))]
        return [(targets[0], _fmt(x)), (targets[1], _fmt(y))]

    raise ValueError(f"unknown transform '{kind}'")


def convert_blob(text, rules):
    """Rewrite ResolveFX nodes into native equivalents.

    Returns (new_text, report). The report always lists what was converted, what
    was left alone and why; the caller decides whether that is acceptable.
    """
    report = {"converted": [], "skipped": [], "nested": 0, "blocked": []}
    by_source = {r.get("source"): r for r in rules}

    blocks = [b for b in scan_blocks(text) if b["type"].startswith(RESOLVEFX_PREFIX)]

    # A ResolveFX node nested inside a Group is never a depth-2 block, so it is
    # never edited. Counting raw occurrences is how we notice and say so rather
    # than claiming a clean conversion.
    report["nested"] = max(0, text.count(RESOLVEFX_PREFIX) - len(blocks))

    edits = []
    for block in blocks:
        rule = by_source.get(block["type"])
        if not rule:
            report["skipped"].append((block["name"], block["type"], "no rule"))
            continue
        missing = rule_blocked(rule)
        if missing:
            report["blocked"].append((block["name"], rule.get("id"), missing))
            continue
        try:
            new_inputs = build_inputs(text, block, rule)
        except ValueError as e:
            report["skipped"].append((block["name"], block["type"], str(e)))
            continue
        edits.append((block, rule, new_inputs))
        report["converted"].append((block["name"], block["type"], rule["target"]))

    # Splice right-to-left so earlier offsets stay valid.
    out = text
    for block, rule, new_inputs in sorted(edits, key=lambda e: -e[0]["block_span"][0]):
        if block["inputs_span"]:
            i0, i1 = block["inputs_span"]
            out = out[:i0] + new_inputs + out[i1:]
        t0, t1 = block["type_span"]
        out = out[:t0] + rule["target"] + out[t1:]

    return out, report


def build_inputs(text, block, rule):
    """The replacement body for a node's `Inputs = { ... }`."""
    if not block["inputs_span"]:
        raise ValueError("node has no Inputs block")
    i0, i1 = block["inputs_span"]
    entries = {e["key"]: e for e in split_entries(text, i0, i1)}

    body = text[i0:i1]
    newline = "\r\n" if "\r\n" in text else "\n"
    indent_match = re.search(r"(?:\r?\n)([ \t]+)", body)
    indent = indent_match.group(1) if indent_match else "\t\t\t\t"
    closing = indent[:-1] if len(indent) > 1 else indent

    lines = []

    # The image connection, carried across verbatim apart from its key. This is
    # what keeps the node wired to its upstream; the node's own name is never in
    # an edited span, so references TO it survive automatically.
    image = rule.get("image_input") or {}
    source_entry = entries.get(image.get("from"))
    if source_entry is not None:
        lines.append(f"{indent}{image.get('to')} = {source_entry['value']},")

    for spec in rule.get("params", []):
        values = []
        for key in spec.get("from", []):
            entry = entries.get(key)
            if entry is None:
                values.append(None)
                continue
            if _is_connected(entry["value"]):
                # An animated or connected input cannot be run through the
                # maths - two animated polar inputs do not become two animated
                # cartesian ones without resampling the splines. Refuse.
                raise ValueError(f"'{key}' is animated or connected")
            values.append(_number(entry["value"]))
        if all(v is None for v in values):
            continue                      # absent on the source = left at default
        for target, literal in apply_transform(spec, values):
            lines.append(f"{indent}{target} = Input {{ Value = {literal}, }},")

    for default in rule.get("defaults", []):
        lines.append(f"{indent}{default['to']} = Input {{ Value = {default['literal']}, }},")

    if not lines:
        raise ValueError("nothing mapped")
    return newline + newline.join(lines) + newline + closing


def validate_conversion(original, converted):
    """Problems with a rewritten blob. Empty list means it is safe to use."""
    problems = []

    before = scan_blocks(original)
    after = scan_blocks(converted)
    if [b["name"] for b in before] != [b["name"] for b in after]:
        problems.append("node names or their order changed")

    if any(b["type"].startswith(RESOLVEFX_PREFIX) for b in after
           if b["name"] in {x["name"] for x in before
                            if x["type"].startswith(RESOLVEFX_PREFIX)}
           and b["type"] != next(x["type"] for x in before if x["name"] == b["name"])):
        problems.append("a converted node still carries the ResolveFX prefix")

    names = {b["name"] for b in after}
    refs_before = set(re.findall(r'SourceOp\s*=\s*"([^"]+)"', original))
    refs_after = set(re.findall(r'SourceOp\s*=\s*"([^"]+)"', converted))
    if refs_before != refs_after:
        problems.append("a node connection was changed or lost")
    for ref in refs_after:
        if ref not in names and ref in {b["name"] for b in before}:
            problems.append(f"connection to '{ref}' no longer resolves")
            break

    return problems


def read_setting_file(path):
    # newline="" on both read and write, so the CRLFs Fusion puts on the
    # clipboard survive the round trip untranslated. Python's default universal
    # newline handling would quietly turn them into \n on the way back out,
    # which defeats the point of storing the blob verbatim.
    try:
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
            return f.read(), None
    except OSError as e:
        return None, str(e)


def write_setting_file(path, text):
    try:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return True, None
    except OSError as e:
        return False, str(e)


# --------------------------------------------------------------------------
# small themed widgets
# --------------------------------------------------------------------------

def make_button(parent, text, command, primary=False, width=None):
    base = ACCENT if primary else BTN
    hover = ACCENT_HOVER if primary else BTN_HOVER
    btn = tk.Button(
        parent, text=text, command=command, bg=base, fg="#ffffff" if primary else FG,
        relief="flat", bd=0, padx=S(12), pady=S(6), font=FONT(9),
        activebackground=hover, activeforeground=FG, cursor="hand2",
        highlightthickness=0,
    )
    if width:
        btn.config(width=width)
    btn.bind("<Enter>", lambda e: btn.config(bg=hover))
    btn.bind("<Leave>", lambda e: btn.config(bg=base))
    return btn


class PromptDialog(tk.Toplevel):
    """Dark modal prompt. fields = [(key, label, kind, initial)] where kind is
    'entry', 'combo' (needs values) or 'text'."""

    def __init__(self, parent, title, fields, combo_values=None):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG)
        self.resizable(False, False)
        self.result = None
        self._widgets = {}
        combo_values = combo_values or {}

        body = tk.Frame(self, bg=BG)
        body.pack(padx=S(16), pady=(S(14), S(8)), fill="both", expand=True)

        for row, (key, label, kind, initial) in enumerate(fields):
            tk.Label(body, text=label, bg=BG, fg=SUB, font=FONT(9),
                     anchor="w").grid(row=row * 2, column=0, sticky="w", pady=(S(6), S(2)))
            if kind == "combo":
                w = ttk.Combobox(body, values=combo_values.get(key, []), width=38,
                                 font=FONT(9))
                w.set(initial or "")
            elif kind == "text":
                w = tk.Text(body, height=3, width=40, bg=PANEL, fg=FG, relief="flat",
                            insertbackground=FG, font=FONT(9), padx=S(6), pady=S(4),
                            highlightthickness=1, highlightbackground=BORDER)
                w.insert("1.0", initial or "")
            else:
                w = tk.Entry(body, width=40, bg=PANEL, fg=FG, relief="flat",
                             insertbackground=FG, font=FONT(9),
                             highlightthickness=1, highlightbackground=BORDER)
                w.insert(0, initial or "")
            w.grid(row=row * 2 + 1, column=0, sticky="ew", ipady=S(3))
            self._widgets[key] = (w, kind)

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=S(16), pady=(S(4), S(14)))
        make_button(btns, "Cancel", self.destroy).pack(side="right")
        make_button(btns, "OK", self._ok, primary=True).pack(side="right", padx=(0, S(8)))

        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())

        first = next(iter(self._widgets.values()))[0]
        first.focus_set()

        self.transient(parent)
        self.grab_set()
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + S(120)
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        parent.wait_window(self)

    def _ok(self):
        out = {}
        for key, (w, kind) in self._widgets.items():
            out[key] = w.get("1.0", "end").strip() if kind == "text" else w.get().strip()
        self.result = out
        self.destroy()


# --------------------------------------------------------------------------
# main panel
# --------------------------------------------------------------------------

class FusionClipboard:
    def __init__(self, root, library):
        self.root = root
        self.library = library
        self.prefs = load_prefs()
        self.presets = []
        self.visible = []
        self.current = None
        self._notes_dirty = False

        root.title("Fusion Clipboard")
        root.configure(bg=BG)
        root.minsize(S(880), S(520))
        # Only reuse a saved size if it was saved at this same scale, otherwise
        # a window sized on a 2K screen comes back cramped on a 4K one.
        geo = self.prefs.get("geometry")
        if geo and abs(float(self.prefs.get("geometry_scale", 1.0)) - SCALE) < 0.01:
            try:
                root.geometry(geo)
            except Exception:
                pass

        self._style()
        self._build()
        self.refresh()

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.bind("<Control-f>", lambda e: self.search_entry.focus_set())

    def rebuild(self):
        """Tear down and rebuild the UI — used when the scale changes."""
        keep_search = self.search_var.get() if hasattr(self, "search_var") else ""
        for child in self.root.winfo_children():
            child.destroy()
        self.root.minsize(S(880), S(520))
        self._style()
        self._build()
        self.search_var.set(keep_search)
        self.refresh()

    def on_scale_change(self, event=None):
        global SCALE
        if event and getattr(event, "widget", None):
            try:
                event.widget.selection_clear()  # drop the highlight after picking
            except Exception:
                pass
        choice = self.scale_var.get()
        self.prefs["ui_scale"] = choice
        save_prefs(self.prefs)
        SCALE = resolve_scale(self.prefs, self.root)
        self.rebuild()
        self.say(f"UI scale set to {choice} ({SCALE:.2f}x).")

    # -- theming ----------------------------------------------------------
    def _style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")  # required before Treeview colors take effect
        except Exception:
            pass
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=FG, borderwidth=0, rowheight=S(23),
                        font=FONT(9))
        style.configure("Treeview.Heading", background=PANEL2, foreground=SUB,
                        relief="flat", font=FONT(8, "bold"), padding=S(4))
        style.map("Treeview.Heading", background=[("active", PANEL2)])
        style.map("Treeview", background=[("selected", ACCENT)],
                  foreground=[("selected", "#ffffff")])
        style.configure("Vertical.TScrollbar", background=BTN, troughcolor=BG,
                        bordercolor=BG, arrowcolor=SUB, relief="flat",
                        arrowsize=S(14), width=S(14))

        # A readonly ttk Combobox draws its text as "selected", so without
        # explicit state maps it lands white-on-white. The dropdown popup is a
        # plain Tk listbox that ttk styling never reaches — themed separately.
        style.configure("TCombobox",
                        fieldbackground=PANEL, background=BTN, foreground=FG,
                        arrowcolor=SUB, bordercolor=BORDER,
                        lightcolor=PANEL, darkcolor=PANEL,
                        selectbackground=PANEL, selectforeground=FG,
                        arrowsize=S(14), padding=S(3))
        style.map("TCombobox",
                  fieldbackground=[("readonly", PANEL), ("disabled", PANEL),
                                   ("focus", PANEL), ("!disabled", PANEL)],
                  foreground=[("readonly", FG), ("disabled", SUB),
                              ("focus", FG), ("!disabled", FG)],
                  selectbackground=[("readonly", PANEL), ("focus", PANEL),
                                    ("!disabled", PANEL)],
                  selectforeground=[("readonly", FG), ("focus", FG),
                                    ("!disabled", FG)],
                  background=[("active", BTN_HOVER), ("readonly", BTN)],
                  bordercolor=[("focus", ACCENT)],
                  arrowcolor=[("active", FG), ("disabled", BORDER)])

        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.root.option_add("*TCombobox*Listbox.font", FONT(9))

    # -- layout -----------------------------------------------------------
    def _build(self):
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=S(12), pady=(S(12), S(8)))

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *a: self.apply_filter())
        search = tk.Entry(top, textvariable=self.search_var, bg=PANEL, fg=FG,
                          relief="flat", insertbackground=FG, font=FONT(10),
                          highlightthickness=1, highlightbackground=BORDER,
                          highlightcolor=ACCENT)
        search.pack(side="left", fill="x", expand=True, ipady=S(5), padx=(0, S(8)))
        self.search_entry = search
        tk.Label(top, text="Search  (Ctrl+F)", bg=BG, fg=SUB,
                 font=FONT(8)).pack(side="left", padx=(0, S(12)))
        make_button(top, "+  Save Clipboard", self.save_selection, primary=True).pack(side="right")

        middle = tk.Frame(self.root, bg=BG)
        middle.pack(fill="both", expand=True, padx=S(12))

        # folder tree
        left = tk.Frame(middle, bg=BG, width=S(190))
        left.pack(side="left", fill="y", padx=(0, S(10)))
        left.pack_propagate(False)
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.apply_filter())

        # preset list
        centre = tk.Frame(middle, bg=BG)
        centre.pack(side="left", fill="both", expand=True)
        cols = ("name", "nodes", "folder")
        self.list = ttk.Treeview(centre, columns=cols, show="headings", selectmode="browse")
        self.list.heading("name", text="PRESET")
        self.list.heading("nodes", text="NODES")
        self.list.heading("folder", text="FOLDER")
        self.list.column("name", width=S(240), anchor="w")
        self.list.column("nodes", width=S(180), anchor="w")
        self.list.column("folder", width=S(120), anchor="w")
        vs = ttk.Scrollbar(centre, orient="vertical", command=self.list.yview)
        self.list.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        self.list.pack(side="left", fill="both", expand=True)
        self.list.bind("<<TreeviewSelect>>", self.on_select)
        self.list.bind("<Double-1>", lambda e: self.paste())
        self.list.bind("<Return>", lambda e: self.paste())
        self.list.bind("<Delete>", lambda e: self.delete())
        self.list.bind("<F2>", lambda e: self.rename())

        # details
        right = tk.Frame(middle, bg=BG, width=S(250))
        right.pack(side="left", fill="y", padx=(S(10), 0))
        right.pack_propagate(False)
        self.detail_name = tk.Label(right, text="—", bg=BG, fg=FG, anchor="w",
                                    font=FONT(11, "bold"), wraplength=S(240),
                                    justify="left")
        self.detail_name.pack(fill="x")
        self.detail_meta = tk.Label(right, text="", bg=BG, fg=SUB, anchor="w",
                                    font=FONT(8), justify="left",
                                    wraplength=S(240))
        self.detail_meta.pack(fill="x", pady=(S(2), S(8)))
        tk.Label(right, text="NOTES", bg=BG, fg=SUB,
                 font=FONT(8, "bold"), anchor="w").pack(fill="x")
        self.notes = tk.Text(right, height=8, bg=PANEL, fg=FG, relief="flat",
                             insertbackground=FG, font=FONT(9), padx=S(6), pady=S(5),
                             wrap="word", highlightthickness=1, highlightbackground=BORDER)
        self.notes.pack(fill="both", expand=True, pady=(S(4), S(6)))
        self.notes.bind("<KeyRelease>", lambda e: setattr(self, "_notes_dirty", True))
        make_button(right, "Save Notes", self.save_notes).pack(fill="x")

        # actions
        actions = tk.Frame(self.root, bg=BG)
        actions.pack(fill="x", padx=S(12), pady=(S(10), S(4)))
        make_button(actions, "Copy to Clipboard", self.paste, primary=True).pack(side="left")
        # Not primary: the verbatim copy stays the default, Studio-safe is the
        # occasional path.
        make_button(actions, "Copy Studio-safe",
                    self.paste_studio_safe).pack(side="left", padx=S(6))
        make_button(actions, "Update from Clipboard",
                    self.update_from_clipboard).pack(side="left", padx=S(6))
        self.fav_btn = make_button(actions, "☆ Favorite", self.toggle_favorite)
        self.fav_btn.pack(side="left", padx=S(6))
        make_button(actions, "Rename", self.rename).pack(side="left", padx=(0, S(6)))
        make_button(actions, "Move", self.move).pack(side="left", padx=(0, S(6)))
        make_button(actions, "Delete", self.delete).pack(side="left")
        make_button(actions, "⟳", self.refresh).pack(side="right")
        make_button(actions, "Learn Node", self.learn_node).pack(side="right", padx=S(6))
        make_button(actions, "Open Folder", self.open_folder).pack(side="right", padx=S(6))
        make_button(actions, "New Folder", self.new_folder).pack(side="right")

        bottom = tk.Frame(self.root, bg=PANEL2)
        bottom.pack(fill="x", side="bottom")
        self.status = tk.Label(bottom, text="", bg=PANEL2, fg=SUB, anchor="w",
                               font=FONT(8), padx=S(12), pady=S(5))
        self.status.pack(side="left", fill="x", expand=True)
        tk.Label(bottom, text="UI scale", bg=PANEL2, fg=SUB,
                 font=FONT(8)).pack(side="left", padx=(0, S(6)))
        self.scale_var = tk.StringVar(value=self.prefs.get("ui_scale", "Auto"))
        scale_box = ttk.Combobox(bottom, textvariable=self.scale_var, values=SCALE_CHOICES,
                                 state="readonly", width=6, font=FONT(8))
        scale_box.pack(side="left", padx=(0, S(10)), pady=S(3))
        scale_box.bind("<<ComboboxSelected>>", self.on_scale_change)

    def say(self, msg, error=False):
        self.status.config(text=msg, fg="#e07070" if error else SUB)

    # -- library scanning -------------------------------------------------
    def scan(self):
        presets = []
        for dirpath, _dirs, files in os.walk(self.library):
            for fn in files:
                if not fn.lower().endswith(".setting"):
                    continue
                full = os.path.join(dirpath, fn)
                rel_dir = os.path.relpath(dirpath, self.library)
                rel_dir = "" if rel_dir == "." else rel_dir.replace("\\", "/")
                name = os.path.splitext(fn)[0]
                presets.append({
                    "name": name,
                    "folder": rel_dir,
                    "rel": f"{rel_dir}/{name}" if rel_dir else name,
                    "path": full,
                    "meta": read_meta(full),
                })
        presets.sort(key=lambda p: (p["folder"].lower(), p["name"].lower()))
        return presets

    def folders(self):
        out = []
        for dirpath, _dirs, _files in os.walk(self.library):
            rel = os.path.relpath(dirpath, self.library)
            if rel != ".":
                out.append(rel.replace("\\", "/"))
        return sorted(out)

    def refresh(self):
        self.presets = self.scan()
        self.tree.delete(*self.tree.get_children())
        self.tree.insert("", "end", ALL_NODE, text=f"  All Presets ({len(self.presets)})")
        favs = sum(1 for p in self.presets if p["meta"].get("favorite"))
        self.tree.insert("", "end", FAV_NODE, text=f"  ★ Favorites ({favs})")
        self.tree.insert("", "end", RECENT_NODE, text="  ↺ Recent")
        for folder in self.folders():
            parent = folder.rsplit("/", 1)[0] if "/" in folder else ""
            label = folder.rsplit("/", 1)[-1]
            try:
                self.tree.insert(parent, "end", folder, text=f"  {label}", open=True)
            except Exception:
                self.tree.insert("", "end", folder, text=f"  {folder}", open=True)
        if not self.tree.selection():
            self.tree.selection_set(ALL_NODE)
        self.apply_filter()
        self.say(f"{len(self.presets)} preset(s) · {self.library}")

    # -- filtering / listing ---------------------------------------------
    def apply_filter(self):
        query = self.search_var.get().strip().lower()
        sel = self.tree.selection()
        node = sel[0] if sel else ALL_NODE

        if query:
            pool = self.presets  # search is always global
        elif node == FAV_NODE:
            pool = [p for p in self.presets if p["meta"].get("favorite")]
        elif node == RECENT_NODE:
            order = self.prefs.get("recent", [])
            by_rel = {p["rel"]: p for p in self.presets}
            pool = [by_rel[r] for r in order if r in by_rel]
        elif node == ALL_NODE:
            pool = self.presets
        else:
            pool = [p for p in self.presets
                    if p["folder"] == node or p["folder"].startswith(node + "/")]

        if query:
            def hit(p):
                hay = " ".join([
                    p["name"], p["folder"],
                    " ".join(p["meta"].get("tools", [])),
                    p["meta"].get("notes", ""),
                ]).lower()
                return query in hay
            pool = [p for p in pool if hit(p)]

        self.visible = pool
        self.list.delete(*self.list.get_children())
        for i, p in enumerate(pool):
            star = "★ " if p["meta"].get("favorite") else ""
            nodes = " + ".join(p["meta"].get("tools", [])) or "—"
            if len(nodes) > 34:
                nodes = nodes[:32] + "…"
            self.list.insert("", "end", iid=str(i),
                             values=(star + p["name"], nodes, p["folder"] or "—"))
        self.current = None
        self.show_details(None)

    def on_select(self, _event=None):
        if self._notes_dirty:
            self.save_notes(quiet=True)
        sel = self.list.selection()
        if not sel:
            return
        idx = int(sel[0])
        if 0 <= idx < len(self.visible):
            self.current = self.visible[idx]
            self.show_details(self.current)

    def show_details(self, preset):
        if not preset:
            self.detail_name.config(text="—")
            self.detail_meta.config(text="")
            self.notes.delete("1.0", "end")
            self.fav_btn.config(text="☆ Favorite")
            self._notes_dirty = False
            return
        meta = preset["meta"]
        self.detail_name.config(text=preset["name"])
        bits = []
        if meta.get("tools"):
            bits.append(f"{len(meta['tools'])} node(s):  " + " + ".join(meta["tools"]))
        if preset["folder"]:
            bits.append(f"Folder:  {preset['folder']}")
        if meta.get("created"):
            bits.append(f"Saved:  {meta['created']}")
        if meta.get("updated"):
            bits.append(f"Updated:  {meta['updated']}")
        self.detail_meta.config(text="\n".join(bits))
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", meta.get("notes", ""))
        self.fav_btn.config(text="★ Favorited" if meta.get("favorite") else "☆ Favorite")
        self._notes_dirty = False

    # -- actions ----------------------------------------------------------
    def save_selection(self):
        text, err = get_clipboard_text()
        if not text:
            self.say(f"Nothing to save — {err}. Select your nodes in Fusion and "
                     f"press Ctrl+C first.", True)
            return

        if not looks_like_fusion_nodes(text):
            preview = text.strip().replace("\n", " ")[:60]
            if not messagebox.askyesno(
                    "Fusion Clipboard",
                    "The clipboard doesn't look like Fusion nodes:\n\n"
                    f"  {preview}...\n\nSave it anyway?",
                    parent=self.root):
                return

        types = parse_tool_types(text)
        sel_node = self.tree.selection()
        default_folder = ""
        if sel_node and sel_node[0] not in (ALL_NODE, FAV_NODE, RECENT_NODE):
            default_folder = sel_node[0]

        summary = f"{len(types)} node(s): {' + '.join(types)}" if types else "clipboard text"
        dlg = PromptDialog(
            self.root, "Save Clipboard",
            [("name", f"Preset name   ({summary})", "entry", ""),
             ("folder", "Folder   (type a new name to create one)", "combo", default_folder),
             ("notes", "Notes   (optional)", "text", "")],
            combo_values={"folder": [""] + self.folders()},
        )
        if not dlg.result or not dlg.result["name"]:
            return

        name = sanitize(dlg.result["name"])
        folder = dlg.result["folder"].strip().strip("/")
        target_dir = os.path.join(self.library, *folder.split("/")) if folder else self.library
        os.makedirs(target_dir, exist_ok=True)
        path = os.path.join(target_dir, name + ".setting")

        if os.path.exists(path) and not messagebox.askyesno(
                "Fusion Clipboard", f"'{name}' already exists in that folder.\n\nOverwrite it?",
                parent=self.root):
            return

        ok, werr = write_setting_file(path, text)
        if not ok:
            self.say(f"Could not write '{name}': {werr}", True)
            return

        write_meta(path, {
            "notes": dlg.result["notes"],
            "tools": types,
            "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "favorite": False,
        })
        self.refresh()
        self.say(f"Saved '{name}' ({summary}) to {folder or 'the library root'}.")

    def paste(self):
        """Put the preset on the clipboard. Fusion is never touched — you press
        Ctrl+V yourself, which is what keeps Group controls intact."""
        if not self.current:
            self.say("Select a preset to copy.", True)
            return

        text, err = read_setting_file(self.current["path"])
        if text is None:
            self.say(f"Could not read the preset file: {err}", True)
            return

        ok, cerr = set_clipboard_text(text)
        if not ok:
            self.say(f"Could not reach the clipboard: {cerr}", True)
            return

        recent = [r for r in self.prefs.get("recent", []) if r != self.current["rel"]]
        recent.insert(0, self.current["rel"])
        self.prefs["recent"] = recent[:RECENT_MAX]
        save_prefs(self.prefs)
        self.say(f"'{self.current['name']}' is on the clipboard — "
                 f"switch to Fusion and press Ctrl+V.")

    def paste_studio_safe(self):
        """Convert ResolveFX nodes to native ones and put THAT on the clipboard.

        The stored preset is never touched: it is the authored original, the
        conversion is lossy, and backup_path() is a single slot - converting over
        the top would destroy the only thing left to re-calibrate against.
        """
        if not self.current:
            self.say("Select a preset to copy.", True)
            return

        text, err = read_setting_file(self.current["path"])
        if text is None:
            self.say(f"Could not read the preset file: {err}", True)
            return

        rules, problems = load_rules()
        if problems:
            self.say("Rules file problem — " + "; ".join(problems), True)
            return

        converted, report = convert_blob(text, rules)

        if not report["converted"] and not report["skipped"] \
                and not report["blocked"] and not report["nested"]:
            ok, cerr = set_clipboard_text(text)
            if not ok:
                self.say(f"Could not reach the clipboard: {cerr}", True)
                return
            self.say(f"'{self.current['name']}' is on the clipboard — it has no "
                     f"ResolveFX nodes, so this is the same as a normal copy.")
            return

        if report["blocked"]:
            name, rule_id, missing = report["blocked"][0]
            self.say(f"Nothing copied — the '{rule_id}' rule still has unverified "
                     f"values ({missing[0]}). Run Learn Node on the native tool "
                     f"first.", True)
            return

        # Only confirm when the result would be imperfect; a clean conversion is
        # one silent click.
        warnings = []
        for name, kind, why in report["skipped"]:
            warnings.append(f"  {name} ({kind.rsplit('.', 1)[-1]}) — {why}")
        if report["nested"]:
            warnings.append(f"  {report['nested']} ResolveFX node(s) inside a Group "
                            f"— convert those inside the Group in Fusion")
        if warnings:
            done = "\n".join(f"  {n} → {t}" for n, _s, t in report["converted"]) \
                   or "  (none)"
            if not messagebox.askyesno(
                    "Fusion Clipboard",
                    f"Converted:\n{done}\n\nStill ResolveFX, will be missing in "
                    f"Fusion Studio:\n" + "\n".join(warnings) + "\n\nCopy anyway?",
                    parent=self.root):
                self.say("Cancelled — nothing copied.")
                return

        if report["converted"]:
            # Never hand over a blob that failed its own checks.
            issues = validate_conversion(text, converted)
            if issues:
                self.say("Conversion rejected by its own checks (" + issues[0]
                         + ") — nothing copied, the clipboard is untouched.", True)
                print("Studio-safe validation failed:")
                for issue in issues:
                    print(f"  {issue}")
                return

        ok, cerr = set_clipboard_text(converted)
        if not ok:
            self.say(f"Could not reach the clipboard: {cerr}", True)
            return

        bits = [f"{len(report['converted'])} converted"]
        if report["skipped"]:
            bits.append(f"{len(report['skipped'])} left as ResolveFX")
        if report["nested"]:
            bits.append(f"{report['nested']} nested, untouched")
        self.say(f"'{self.current['name']}' copied Studio-safe — " + ", ".join(bits)
                 + ".  Switch to Fusion and press Ctrl+V.",
                 error=bool(report["skipped"] or report["nested"]))

    def learn_node(self):
        """Report a clipboard node's real schema, so rules are written from fact.

        This exists because a wrong target input name cannot be detected from the
        text — Fusion accepts unknown inputs silently and pastes the node at its
        defaults. The only defence is capturing the real names first.
        """
        text, err = get_clipboard_text()
        if not text:
            self.say(f"Nothing on the clipboard — {err}. Copy a node in Fusion "
                     f"with Ctrl+C first.", True)
            return

        blocks = scan_blocks(text)
        if not blocks:
            self.say("No Fusion nodes found on the clipboard.", True)
            return

        lines = [
            "Fusion Clipboard - Learn Node",
            "",
            "NOTE: Fusion only writes out inputs that are OFF their default, so a",
            "node left at defaults looks almost empty here. Nudge every control",
            "you care about before copying, or the capture will be misleading.",
            "",
        ]
        for block in blocks:
            lines.append("=" * 68)
            lines.append(f"name        {block['name']}")
            lines.append(f"type        {block['type']}")
            if not block["inputs_span"]:
                lines.append("inputs      (none serialised)")
                lines.append("")
                continue
            entries = split_entries(text, *block["inputs_span"])
            lines.append(f"inputs      {len(entries)}")
            for entry in entries:
                value = " ".join(entry["value"].split())
                if len(value) > 68:
                    value = value[:66] + "..."
                tag = "connected" if _is_connected(entry["value"]) else "const    "
                lines.append(f"  {tag}  {entry['key']:<28} {value}")
            image = [e["key"] for e in entries if _is_connected(e["value"])]
            if image:
                lines.append(f"image input likely: {image[0]}")
            lines.append("")

        report = "\n".join(lines)
        print("\n" + report)

        try:
            store = {}
            if os.path.exists(RULES_FILE):
                with open(RULES_FILE, "r", encoding="utf-8") as f:
                    store = json.load(f)
            captures = store.setdefault("captures", {})
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            for block in blocks:
                key = f"{block['type']}@{stamp}"
                captures[key] = {
                    "name": block["name"],
                    "type": block["type"],
                    "inputs": [
                        {"key": e["key"], "value": " ".join(e["value"].split()),
                         "connected": _is_connected(e["value"])}
                        for e in (split_entries(text, *block["inputs_span"])
                                  if block["inputs_span"] else [])
                    ],
                }
            with open(RULES_FILE, "w", encoding="utf-8") as f:
                json.dump(store, f, indent=2)
        except Exception as e:
            print(f"Could not record the capture: {e}")

        set_clipboard_text(report)
        self.say(f"Learned {len(blocks)} node(s) — report printed to the console, "
                 f"saved to {os.path.basename(RULES_FILE)}, and copied to the "
                 f"clipboard so you can paste it.")

    def update_from_clipboard(self):
        """Replace a preset's contents, keeping its name, folder, notes and
        favorite flag. Saves you deleting and re-saving to revise a template.

        No confirmation dialog - the previous version is written to a .bak
        beside it instead, which is both quicker and safer than a yes/no box.
        Scanning only picks up .setting files, so the backup never shows in the
        list.
        """
        if not self.current:
            self.say("Select the preset you want to update.", True)
            return

        text, err = get_clipboard_text()
        if not text:
            self.say(f"Nothing to update from — {err}. Copy the new version in "
                     f"Fusion with Ctrl+C first.", True)
            return

        types = parse_tool_types(text)
        if looks_like_fusion_nodes(text):
            what = " + ".join(types) if types else "the clipboard contents"
            question = (f"Replace '{self.current['name']}' with {what}?\n\n"
                        "Its name, folder, notes and favorite are kept. The old "
                        "version is saved beside it as a .bak.")
        else:
            preview = text.strip().replace("\n", " ")[:60]
            question = ("The clipboard doesn't look like Fusion nodes:\n\n"
                        f"  {preview}...\n\n"
                        f"Replace '{self.current['name']}' with it anyway?")

        if not messagebox.askyesno("Fusion Clipboard", question, parent=self.root):
            self.say("Update cancelled — nothing changed.")
            return

        path = self.current["path"]
        backup = backup_path(path)
        try:
            if os.path.exists(path):
                shutil.copy2(path, backup)
        except OSError as e:
            self.say(f"Could not back up the old version ({e}) — nothing changed.", True)
            return

        ok, werr = write_setting_file(path, text)
        if not ok:
            self.say(f"Could not write the update: {werr}", True)
            return

        meta = dict(self.current["meta"])        # keep notes / favorite / created
        meta["tools"] = types
        meta["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        write_meta(path, meta)

        name = self.current["name"]
        rel = self.current["rel"]
        self.refresh()
        for i, p in enumerate(self.visible):     # keep it selected after the rescan
            if p["rel"] == rel:
                self.list.selection_set(str(i))
                self.list.see(str(i))
                break
        summary = f"{len(types)} node(s): {' + '.join(types)}" if types else "clipboard text"
        self.say(f"Updated '{name}' — {summary}. Previous version kept as "
                 f"{os.path.basename(backup)}.")

    def toggle_favorite(self):
        if not self.current:
            return
        meta = self.current["meta"]
        meta["favorite"] = not meta.get("favorite")
        write_meta(self.current["path"], meta)
        rel = self.current["rel"]
        self.refresh()
        for i, p in enumerate(self.visible):
            if p["rel"] == rel:
                self.list.selection_set(str(i))
                break
        self.say(("Added to" if meta["favorite"] else "Removed from") + " favorites.")

    def save_notes(self, quiet=False):
        if not self.current:
            return
        meta = self.current["meta"]
        meta["notes"] = self.notes.get("1.0", "end").strip()
        write_meta(self.current["path"], meta)
        self._notes_dirty = False
        if not quiet:
            self.say(f"Notes saved for '{self.current['name']}'.")

    def rename(self):
        if not self.current:
            return
        dlg = PromptDialog(self.root, "Rename Preset",
                           [("name", "New name", "entry", self.current["name"])])
        if not dlg.result or not dlg.result["name"]:
            return
        new = sanitize(dlg.result["name"])
        folder = os.path.dirname(self.current["path"])
        new_path = os.path.join(folder, new + ".setting")
        if os.path.exists(new_path):
            self.say(f"'{new}' already exists in that folder.", True)
            return
        try:
            os.rename(self.current["path"], new_path)
            for old, new_name in zip(companion_paths(self.current["path"]),
                                     companion_paths(new_path)):
                if os.path.exists(old):
                    os.rename(old, new_name)
        except OSError as e:
            self.say(f"Rename failed: {e}", True)
            return
        self.refresh()
        self.say(f"Renamed to '{new}'.")

    def move(self):
        if not self.current:
            return
        dlg = PromptDialog(self.root, "Move Preset",
                           [("folder", "Destination folder   (blank = library root)",
                             "combo", self.current["folder"])],
                           combo_values={"folder": [""] + self.folders()})
        if dlg.result is None:
            return
        folder = dlg.result["folder"].strip().strip("/")
        target_dir = os.path.join(self.library, *folder.split("/")) if folder else self.library
        os.makedirs(target_dir, exist_ok=True)
        new_path = os.path.join(target_dir, self.current["name"] + ".setting")
        if os.path.normcase(new_path) == os.path.normcase(self.current["path"]):
            return
        if os.path.exists(new_path):
            self.say("A preset with that name already exists there.", True)
            return
        try:
            shutil.move(self.current["path"], new_path)
            for old, new_name in zip(companion_paths(self.current["path"]),
                                     companion_paths(new_path)):
                if os.path.exists(old):
                    shutil.move(old, new_name)
        except OSError as e:
            self.say(f"Move failed: {e}", True)
            return
        self.refresh()
        self.say(f"Moved to {folder or 'the library root'}.")

    def delete(self):
        if not self.current:
            return
        if not messagebox.askyesno(
                "Fusion Clipboard",
                f"Delete '{self.current['name']}' permanently?\n\n{self.current['path']}",
                parent=self.root):
            return
        try:
            os.remove(self.current["path"])
            for companion in companion_paths(self.current["path"]):
                if os.path.exists(companion):
                    os.remove(companion)
        except OSError as e:
            self.say(f"Delete failed: {e}", True)
            return
        name = self.current["name"]
        self.refresh()
        self.say(f"Deleted '{name}'.")

    def new_folder(self):
        sel = self.tree.selection()
        parent = ""
        if sel and sel[0] not in (ALL_NODE, FAV_NODE, RECENT_NODE):
            parent = sel[0]
        dlg = PromptDialog(self.root, "New Folder",
                           [("name", f"Folder name   (inside '{parent or 'library root'}')",
                             "entry", "")])
        if not dlg.result or not dlg.result["name"]:
            return
        rel = f"{parent}/{sanitize(dlg.result['name'])}" if parent else sanitize(dlg.result["name"])
        try:
            os.makedirs(os.path.join(self.library, *rel.split("/")), exist_ok=True)
        except OSError as e:
            self.say(f"Could not create folder: {e}", True)
            return
        self.refresh()
        self.say(f"Created folder '{rel}'.")

    def open_folder(self):
        target = os.path.dirname(self.current["path"]) if self.current else self.library
        try:
            os.startfile(target)
        except Exception as e:
            self.say(f"Could not open folder: {e}", True)

    def _on_close(self):
        if self._notes_dirty:
            self.save_notes(quiet=True)
        try:
            self.prefs["geometry"] = self.root.geometry()
            self.prefs["geometry_scale"] = round(SCALE, 3)
            save_prefs(self.prefs)
        except Exception:
            pass
        self.root.destroy()


# --------------------------------------------------------------------------
# bootstrap
# --------------------------------------------------------------------------

prefs = load_prefs()
library = prefs.get("library_path") or DEFAULT_LIBRARY
os.makedirs(library, exist_ok=True)

root = tk.Tk()
SCALE = resolve_scale(prefs, root)
app = FusionClipboard(root, library)
root.bind("<Escape>", lambda e: app._on_close())
root.lift()
root.attributes("-topmost", True)
root.after(300, lambda: root.attributes("-topmost", False))
root.mainloop()
