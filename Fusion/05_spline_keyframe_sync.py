import ctypes
import json
import math
import os
import tkinter as tk
import traceback
from tkinter import ttk

# Spline Keyframe Sync - put the punch of an ease exactly on the playhead, and
# nudge keys by exact frame counts.
#
# Two separate jobs:
#
#   Sync ease   RESHAPES the easing without moving anything. Keyframe times and
#               values stay exactly where they are; only the bezier handles are
#               redistributed, so the moment the curve is moving fastest (or
#               accelerating hardest) lands on the playhead. That is the hit
#               you are lining up to a beat.
#   Nudge       shifts keys by a whole number of frames - the thing that is
#               miserable to do by dragging in the Spline editor.
#
# How the reshape works: a segment between two keys is a cubic bezier. The two
# handles carry a total "ease budget" which is kept constant (the sum of the
# two handle scales is always 2, so an even split leaves the curve untouched).
# Shifting the budget towards the outgoing handle flattens the start and pushes
# the peak later; towards the incoming handle pulls it earlier. The split is
# solved by bisection until the peak sits on the playhead.
#
# IMPORTANT - what "selected" means here:
#   Fusion's scripting API exposes selected TOOLS (comp.GetToolList(True)) but
#   NOT selected keyframes. Nothing can see a marquee in the Spline editor. The
#   scope is therefore the selected nodes, and the segment acted on is the one
#   under the playhead.
#
# Run this from the Fusion page with a comp open.

for _mod, _fn, _arg in (("shcore", "SetProcessDpiAwareness", 2),
                        ("shcore", "SetProcessDpiAwareness", 1),
                        ("user32", "SetProcessDPIAware", None)):
    try:
        _f = getattr(getattr(ctypes.windll, _mod), _fn)
        _f(_arg) if _arg is not None else _f()
        break
    except Exception:
        continue

PREFS_FILE = os.path.expandvars(r"%APPDATA%\spline_keyframe_sync_prefs.json")

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

SCALE_CHOICES = ["Auto", "100%", "125%", "150%", "175%", "200%", "250%"]
SCALE = 1.0

CURVE_SAMPLES = 400     # samples per segment when locating the peak
SPLIT_MIN = 0.03        # how lopsided the handle split may get
SPLIT_MAX = 0.97
BISECT_STEPS = 40
PEAK_MODES = ["Fastest point (max speed)", "Hardest acceleration"]
NUDGE_SCOPES = ["All keyframes", "From the playhead onward"]


def S(n):
    return max(1, int(round(n * SCALE)))


def FONT(size, weight=None):
    pts = max(6, int(round(size * SCALE)))
    return ("Segoe UI", pts, weight) if weight else ("Segoe UI", pts)


def detect_scale(root):
    try:
        dpi = root.winfo_fpixels("1i")
    except Exception:
        dpi = 96.0
    scale = dpi / 96.0
    if scale < 1.05:
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


def make_button(parent, text, command, primary=False, width=None):
    base = ACCENT if primary else BTN
    hover = ACCENT_HOVER if primary else BTN_HOVER
    btn = tk.Button(parent, text=text, command=command, bg=base,
                    fg="#ffffff" if primary else FG, relief="flat", bd=0,
                    padx=S(10), pady=S(6), font=FONT(9), cursor="hand2",
                    activebackground=hover, activeforeground=FG,
                    highlightthickness=0)
    if width:
        btn.config(width=width)
    btn.bind("<Enter>", lambda e: btn.config(bg=hover))
    btn.bind("<Leave>", lambda e: btn.config(bg=base))
    return btn


# --------------------------------------------------------------------------
# Fusion bridge
# --------------------------------------------------------------------------

fusion = bmd.scriptapp("Fusion")


def current_comp():
    try:
        return fusion.GetCurrentComp()
    except Exception:
        return None


def playhead(comp):
    try:
        return float(comp.CurrentTime)
    except Exception:
        return 0.0


def animated_inputs(tool):
    """[(input_id, name, spline_tool)] for every BezierSpline-driven input.

    PolyPath and Tracker animations are skipped on purpose: their keyframe
    tables are not the flat frame -> value shape this works on, and mangling
    them silently would be worse than leaving them alone.
    """
    found = []
    try:
        inputs = tool.GetInputList() or {}
    except Exception:
        return found
    for inp in inputs.values():
        try:
            attrs = inp.GetAttrs() or {}
        except Exception:
            continue
        if str(attrs.get("INPS_DataType") or "") == "Image":
            continue
        try:
            output = inp.GetConnectedOutput()
        except Exception:
            continue
        if not output:
            continue
        try:
            spline = output.GetTool()
            if not spline or spline.ID != "BezierSpline":
                continue
        except Exception:
            continue
        input_id = attrs.get("INPS_ID")
        if not input_id:
            continue
        found.append((input_id, attrs.get("INPS_Name") or input_id, spline))
    return found


# --------------------------------------------------------------------------
# keyframe table maths
#
# GetKeyFrames() hands back {frame: {value, LH = {dframe, dvalue},
# RH = {dframe, dvalue}, Flags = {...}}}. The handles are OFFSETS FROM THEIR
# KEY. Treating them as absolute positions throws a handle hundreds of frames
# past the end of the segment and destroys the easing - that is exactly what
# happened the first time this tool was written, so the conversion to and from
# absolute space is done in handle_point/set_handle and nowhere else.
# --------------------------------------------------------------------------

def keyframe_table(spline):
    try:
        keys = spline.GetKeyFrames() or {}
    except Exception:
        return {}
    return {float(k): v for k, v in keys.items() if isinstance(k, (int, float))}


def key_value(data):
    if isinstance(data, dict):
        for probe in (1, 1.0, "1"):
            if probe in data:
                try:
                    return float(data[probe])
                except (TypeError, ValueError):
                    return None
        return None
    try:
        return float(data)
    except (TypeError, ValueError):
        return None


def handle_point(data, which, key_frame, key_value_):
    """A key's LH/RH handle as an ABSOLUTE (frame, value), or None.

    Fusion stores handles as OFFSETS from their key, not as absolute
    positions. Getting this backwards writes a handle hundreds of frames past
    the end of the segment - it is what broke this tool the first time round -
    so the conversion happens here, once, in both directions.
    """
    if not isinstance(data, dict):
        return None
    point = data.get(which)
    pair = None
    if isinstance(point, dict):
        try:
            pair = (float(point[1]), float(point[2]))
        except (KeyError, TypeError, ValueError):
            return None
    elif isinstance(point, (list, tuple)) and len(point) >= 2:
        try:
            pair = (float(point[0]), float(point[1]))
        except (TypeError, ValueError):
            return None
    if pair is None:
        return None
    return key_frame + pair[0], key_value_ + pair[1]


def set_handle(data, which, frame, value, key_frame, key_value_):
    """Write a handle back, converting the absolute point to an offset."""
    point = data.get(which) if isinstance(data, dict) else None
    dt, dv = frame - key_frame, value - key_value_
    if isinstance(point, (list, tuple)):
        return [dt, dv]
    return {1: dt, 2: dv}


def bezier(p0, p1, p2, p3, u):
    m = 1.0 - u
    return (m * m * m * p0 + 3 * m * m * u * p1
            + 3 * m * u * u * p2 + u * u * u * p3)


def bezier_slope(p0, p1, p2, p3, u):
    m = 1.0 - u
    return 3 * m * m * (p1 - p0) + 6 * m * u * (p2 - p1) + 3 * u * u * (p3 - p2)


# --------------------------------------------------------------------------
# baselines
#
# Every reshape is computed from the AUTHORED handles, never from the result of
# the last reshape. An operation that reads its own output as input drifts, and
# this one drifted in the worst direction: each run shaved a little length off
# the handles until the ease was gone and the curve went linear. The authored
# shape is recorded the first time a segment is seen and reused from then on.
# --------------------------------------------------------------------------

BASELINE_FILE = os.path.expandvars(r"%APPDATA%\spline_keyframe_sync_baselines.json")


def load_baselines():
    if os.path.exists(BASELINE_FILE):
        try:
            with open(BASELINE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_baselines(store):
    try:
        with open(BASELINE_FILE, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=1)
    except Exception as e:
        print(f"Could not save baselines: {e}")


def baseline_key(comp_name, spline_name, seg):
    return f"{comp_name}|{spline_name}|{seg['t0']:g}-{seg['t1']:g}"


def attach_baseline(seg, store, key):
    """Give the segment its authored handles, recording them on first sight."""
    entry = store.get(key)
    if not entry and seg["rh"] and seg["lh"]:
        entry = {"rh": list(seg["rh"]), "lh": list(seg["lh"])}
        store[key] = entry
        save_baselines(store)
    if entry:
        seg["brh"] = tuple(entry["rh"])
        seg["blh"] = tuple(entry["lh"])
    else:
        seg["brh"], seg["blh"] = seg["rh"], seg["lh"]
    return seg


def segments_of(spline):
    """Every span between consecutive keys, with its two bezier handles."""
    keys = keyframe_table(spline)
    frames = sorted(keys)
    out = []
    for t0, t1 in zip(frames, frames[1:]):
        v0, v1 = key_value(keys[t0]), key_value(keys[t1])
        if v0 is None or v1 is None or t1 <= t0:
            continue
        seg = {
            "t0": t0, "t1": t1, "v0": v0, "v1": v1,
            "rh": handle_point(keys[t0], "RH", t0, v0),
            "lh": handle_point(keys[t1], "LH", t1, v1),
        }
        seg["brh"], seg["blh"] = seg["rh"], seg["lh"]
        out.append(seg)
    return out


def handle_vectors(seg):
    """The authored handle vectors and their lengths, or None if linear."""
    brh, blh = seg.get("brh"), seg.get("blh")
    if brh is None or blh is None:
        return None
    h0 = (brh[0] - seg["t0"], brh[1] - seg["v0"])
    h1 = (blh[0] - seg["t1"], blh[1] - seg["v1"])
    l0, l1 = math.hypot(*h0), math.hypot(*h1)
    if l0 + l1 <= 1e-9:
        return None
    return h0, h1, l0, l1


def baseline_split(seg):
    """The split that reproduces the authored curve exactly."""
    vectors = handle_vectors(seg)
    if not vectors:
        return None
    _h0, _h1, l0, l1 = vectors
    return l0 / (l0 + l1)


def controls(seg, split=None):
    """The segment's two inner control points at a given handle split.

    What is held constant is the TOTAL HANDLE LENGTH - the amount of ease -
    not a pair of scale factors. Each handle keeps its authored direction and
    only its share of that length changes, so the identity split is the
    authored proportion (not 0.5, which would flatten an asymmetric ease the
    moment you touched it).

    A handle that would reach beyond the segment gets capped, and the surplus
    is handed to the other handle instead of being thrown away. Throwing it
    away is what bled the ease out a little more on every run.
    """
    vectors = handle_vectors(seg)
    if not vectors:
        return None                     # linear segment: no ease to move
    h0, h1, l0, l1 = vectors
    total = l0 + l1
    if split is None:
        split = l0 / total

    t0, v0, t1, v1 = seg["t0"], seg["v0"], seg["t1"], seg["v1"]
    span = t1 - t0

    def unit(vector, length, fallback):
        return (vector[0] / length, vector[1] / length) if length > 1e-9 else fallback

    u0 = unit(h0, l0, (1.0, 0.0))
    u1 = unit(h1, l1, (-1.0, 0.0))

    def cap(length, direction):
        """Trim to what fits inside the span; return (kept, surplus)."""
        if abs(direction[0]) < 1e-9:
            return length, 0.0
        limit = (0.98 * span) / abs(direction[0])
        return (limit, length - limit) if length > limit else (length, 0.0)

    len0, spill = cap(total * split, u0)
    len1, spill = cap(total * (1.0 - split) + spill, u1)
    if spill > 0:
        len0, _ = cap(len0 + spill, u0)

    p1t, p1v = t0 + u0[0] * len0, v0 + u0[1] * len0
    p2t, p2v = t1 + u1[0] * len1, v1 + u1[1] * len1
    # Keeping both control times inside the span is what stops the curve
    # doubling back on itself in time, which would make the value undefined.
    p1t = min(max(p1t, t0), t1)
    p2t = min(max(p2t, t0), t1)
    return p1t, p1v, p2t, p2v


def current_controls(seg):
    """Control points of what is ACTUALLY in the comp right now.

    Distinct from controls(), which always builds from the authored baseline.
    The table has to show the live curve, or you would be looking at a peak
    that is not the one you are about to change.
    """
    if seg["rh"] is None or seg["lh"] is None:
        return None
    t0, t1 = seg["t0"], seg["t1"]
    p1t = min(max(seg["rh"][0], t0), t1)
    p2t = min(max(seg["lh"][0], t0), t1)
    return p1t, seg["rh"][1], p2t, seg["lh"][1]


def peak_speed(seg, ctrl):
    """Highest |dv/dt| on the segment - the strength of the ease."""
    if not ctrl:
        return None
    p1t, p1v, p2t, p2v = ctrl
    t0, v0, t1, v1 = seg["t0"], seg["v0"], seg["t1"], seg["v1"]
    best = 0.0
    for i in range(CURVE_SAMPLES + 1):
        u = i / float(CURVE_SAMPLES)
        dt = bezier_slope(t0, p1t, p2t, t1, u)
        if dt <= 1e-9:
            continue
        best = max(best, abs(bezier_slope(v0, p1v, p2v, v1, u) / dt))
    return best or None


def peak_time(seg, ctrl, mode):
    """The frame within the segment where the curve peaks.

    Speed is dv/dt, so it has to be evaluated as (dv/du)/(dt/du) - the curve is
    not uniform in time, and that non-uniformity IS the easing.
    """
    if not ctrl:
        return None
    p1t, p1v, p2t, p2v = ctrl
    t0, v0, t1, v1 = seg["t0"], seg["v0"], seg["t1"], seg["v1"]
    if abs(v1 - v0) < 1e-12:
        return None                     # nothing moves; no peak to find

    speeds = []
    for i in range(CURVE_SAMPLES + 1):
        u = i / float(CURVE_SAMPLES)
        dt = bezier_slope(t0, p1t, p2t, t1, u)
        if dt <= 1e-9:
            continue
        dv = bezier_slope(v0, p1v, p2v, v1, u)
        speeds.append((bezier(t0, p1t, p2t, t1, u), dv / dt))
    if len(speeds) < 3:
        return None

    if mode == PEAK_MODES[1]:           # hardest acceleration
        # SIGNED, deliberately. Using the magnitude would let the braking at
        # the end of an ease-out win, and worse, the winner would jump between
        # the two humps as the handles move - which makes the peak position
        # discontinuous and the solver below meaningless. Accelerating hardest
        # means speeding up hardest.
        series = []
        for (ta, sa), (tb, sb) in zip(speeds, speeds[1:]):
            dt = tb - ta
            if dt <= 1e-9:
                continue
            series.append(((ta + tb) / 2.0, (sb - sa) / dt))
    else:
        series = [(t, abs(s)) for t, s in speeds]

    if not series:
        return None
    best_t, best_v = max(series, key=lambda pair: pair[1])
    if best_v <= 1e-12:
        return None
    return best_t


def solve_split(seg, target, mode):
    """Find the handle split that puts the peak on 'target'.

    Peak time rises monotonically with the split (a longer outgoing handle
    flattens the start and delays the punch), so bisection is both safe and
    quick. Returns (split, achieved_peak, exact) - 'exact' is False when the
    target is outside what reshaping can reach, in which case the closest
    achievable split is returned rather than nothing.
    """
    low_peak = peak_time(seg, controls(seg, SPLIT_MIN), mode)
    high_peak = peak_time(seg, controls(seg, SPLIT_MAX), mode)
    if low_peak is None or high_peak is None:
        return None, None, False
    if target <= low_peak:
        return SPLIT_MIN, low_peak, abs(target - low_peak) < 0.5
    if target >= high_peak:
        return SPLIT_MAX, high_peak, abs(target - high_peak) < 0.5

    low, high = SPLIT_MIN, SPLIT_MAX
    best = (0.5, None)
    for _ in range(BISECT_STEPS):
        mid = (low + high) / 2.0
        got = peak_time(seg, controls(seg, mid), mode)
        if got is None:
            break
        if best[1] is None or abs(got - target) < abs(best[1] - target):
            best = (mid, got)
        if abs(got - target) < 1e-4:
            break
        if got < target:
            low = mid
        else:
            high = mid
    if best[1] is None:
        return None, None, False
    # Trust the result only if it actually landed. Bisection assumes the peak
    # moves smoothly with the split; if a curve ever breaks that assumption,
    # this reports the near miss instead of quietly claiming success.
    return best[0], best[1], abs(best[1] - target) < 0.5


def apply_split(spline, seg, split):
    """Write the reshaped handles back. Keyframe times and values never move.

    Only the two handles bounding this segment are touched, so the easing on
    the neighbouring segments is left exactly as it was.
    """
    ctrl = controls(seg, split)
    if not ctrl:
        return False
    p1t, p1v, p2t, p2v = ctrl
    keys = keyframe_table(spline)
    start, end = keys.get(seg["t0"]), keys.get(seg["t1"])
    if not isinstance(start, dict) or not isinstance(end, dict):
        return False

    start = dict(start)
    end = dict(end)
    start["RH"] = set_handle(start, "RH", p1t, p1v, seg["t0"], seg["v0"])
    end["LH"] = set_handle(end, "LH", p2t, p2v, seg["t1"], seg["v1"])
    try:
        spline.SetKeyFrames({seg["t0"]: start, seg["t1"]: end}, True)
        return True
    except Exception as e:
        print(f"    SetKeyFrames failed: {e}")
        return False


# --------------------------------------------------------------------------
# backups
#
# Every write is preceded by a full snapshot of the affected splines, because
# Fusion's own undo has not proved reliable for scripted keyframe edits and
# there is no other way back from a bad write.
# --------------------------------------------------------------------------

BACKUP_DIR = os.path.expandvars(r"%APPDATA%\spline_keyframe_sync_backups")
BACKUP_KEEP = 30


def _encode(obj):
    """JSON-safe form of a Fusion table, preserving numeric dict keys."""
    if isinstance(obj, dict):
        return {"__d__": [[_encode(k), _encode(v)] for k, v in obj.items()]}
    if isinstance(obj, (list, tuple)):
        return {"__l__": [_encode(v) for v in obj]}
    if isinstance(obj, (bool, int, float, str)) or obj is None:
        return obj
    return str(obj)


def _decode(obj):
    if isinstance(obj, dict):
        if "__d__" in obj:
            return {_decode(k): _decode(v) for k, v in obj["__d__"]}
        if "__l__" in obj:
            return [_decode(v) for v in obj["__l__"]]
    return obj


def write_backup(splines, label):
    """Snapshot {spline_name: keyframe table}. Returns the file path or None."""
    import datetime
    payload = {}
    for spline in splines:
        try:
            payload[str(spline.Name)] = _encode(spline.GetKeyFrames() or {})
        except Exception as e:
            print(f"    could not snapshot {spline}: {e}")
    if not payload:
        return None
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(BACKUP_DIR, f"{stamp}_{label}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1)
    except Exception as e:
        print(f"    could not write backup: {e}")
        return None

    try:
        files = sorted(os.listdir(BACKUP_DIR))
        for stale in files[:-BACKUP_KEEP]:
            os.remove(os.path.join(BACKUP_DIR, stale))
    except Exception:
        pass
    return path


def list_backups():
    try:
        return sorted(f for f in os.listdir(BACKUP_DIR) if f.endswith(".json"))
    except Exception:
        return []


def restore_backup(comp, filename):
    """Put a snapshot back. Returns (restored, failed)."""
    path = os.path.join(BACKUP_DIR, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        print(f"Could not read {filename}: {e}")
        return 0, 0

    restored = failed = 0
    for name, encoded in payload.items():
        keys = _decode(encoded)
        try:
            spline = comp.FindTool(name)
        except Exception:
            spline = None
        if not spline:
            print(f"  {name}: not in this composition any more")
            failed += 1
            continue
        frames = [k for k in keys if isinstance(k, (int, float))]
        if not frames:
            continue
        try:
            current = spline.GetKeyFrames() or {}
            now = [k for k in current if isinstance(k, (int, float))]
            if now:
                # Clear whatever is there now, including keys the snapshot
                # does not have, so the result is the snapshot exactly.
                spline.DeleteKeyFrames(min(now), max(now))
            spline.SetKeyFrames(keys, True)
            restored += 1
            print(f"  {name}: {len(frames)} key(s) restored")
        except Exception as e:
            failed += 1
            print(f"  {name}: restore FAILED - {e}")
    return restored, failed



def shift_spline(spline, offset, from_frame=None):
    """Move keyframes in time by 'offset' frames.

    AdjustKeyFrames is the tidy path and keeps handles intact. The fallback
    rebuilds the table by hand and has to move the LH/RH coordinates too -
    they are absolute frame positions, so leaving them behind would rip the
    easing off every key.
    """
    if not offset:
        return True
    keys = keyframe_table(spline)
    if not keys:
        return False
    first, last = min(keys), max(keys)
    if from_frame is not None:
        first = max(first, from_frame)
        if first > last:
            return False

    try:
        spline.AdjustKeyFrames(first, last, offset, 0, "offset")
        return True
    except Exception as e:
        print(f"    AdjustKeyFrames failed ({e}); rebuilding the table instead")

    moving = {f: d for f, d in keys.items() if first <= f <= last}
    if not moving:
        return False
    # Handles are stored as offsets from their key, so they travel with it -
    # nothing to adjust here beyond the key's own frame.
    rebuilt = {frame + offset: data for frame, data in moving.items()}
    try:
        # Clear first: overlapping old and new positions would collide and
        # leave a duplicated key at the seam.
        spline.DeleteKeyFrames(first, last)
        spline.SetKeyFrames(rebuilt, True)
        return True
    except Exception as e:
        print(f"    could not rebuild keyframes: {e}")
        return False


# --------------------------------------------------------------------------
# panel
# --------------------------------------------------------------------------

class SplineSync:
    def __init__(self, root):
        self.root = root
        self.prefs = load_prefs()
        self.baselines = load_baselines()
        self.rows = []

        root.title("Spline Keyframe Sync")
        root.configure(bg=BG)
        root.minsize(S(640), S(360))
        geo = self.prefs.get("geometry")
        if geo and abs(float(self.prefs.get("geometry_scale", 1.0)) - SCALE) < 0.01:
            try:
                root.geometry(geo)
            except Exception:
                pass

        self._style()
        self._build()
        self.rescan()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=FG, borderwidth=0, rowheight=S(22), font=FONT(9))
        style.configure("Treeview.Heading", background=PANEL2, foreground=SUB,
                        relief="flat", font=FONT(8, "bold"))
        style.map("Treeview", background=[("selected", ACCENT)],
                  foreground=[("selected", "#ffffff")])
        style.configure("TCombobox", fieldbackground=PANEL, background=BTN,
                        foreground=FG, arrowcolor=SUB, bordercolor=BORDER,
                        lightcolor=PANEL, darkcolor=PANEL,
                        selectbackground=PANEL, selectforeground=FG,
                        arrowsize=S(14), padding=S(3))
        style.map("TCombobox",
                  fieldbackground=[("readonly", PANEL), ("!disabled", PANEL)],
                  foreground=[("readonly", FG), ("!disabled", FG)],
                  selectbackground=[("readonly", PANEL), ("!disabled", PANEL)],
                  selectforeground=[("readonly", FG), ("!disabled", FG)],
                  background=[("active", BTN_HOVER), ("readonly", BTN)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    def _build(self):
        bottom = tk.Frame(self.root, bg=PANEL2)
        bottom.pack(fill="x", side="bottom")

        content = tk.Frame(self.root, bg=BG)
        content.pack(fill="both", expand=True)

        head = tk.Frame(content, bg=BG)
        head.pack(fill="x", padx=S(14), pady=(S(12), S(4)))
        self.comp_label = tk.Label(head, text="—", bg=BG, fg=FG, anchor="w",
                                   font=FONT(11, "bold"))
        self.comp_label.pack(fill="x")
        row = tk.Frame(head, bg=BG)
        row.pack(fill="x", pady=(S(2), 0))
        self.info = tk.Label(row, text="", bg=BG, fg=SUB, anchor="w", font=FONT(9))
        self.info.pack(side="left")
        make_button(row, "Rescan", self.rescan).pack(side="right")
        self.top_btn = tk.Button(row, text="On Top", relief="flat", bd=0, font=FONT(9),
                                 cursor="hand2", padx=S(10), pady=S(5),
                                 highlightthickness=0, command=self.toggle_topmost)
        self.top_btn.pack(side="right", padx=(0, S(6)))
        self._paint_topmost()

        table = tk.Frame(content, bg=BG)
        table.pack(fill="both", expand=True, padx=S(14), pady=(S(10), 0))
        self.tree = ttk.Treeview(table,
                                 columns=("tool", "input", "seg", "peak", "state"),
                                 show="headings", height=8)
        for col, label, width in (("tool", "NODE", 140), ("input", "PARAMETER", 120),
                                  ("seg", "SEGMENT", 110), ("peak", "PEAK", 70),
                                  ("state", "", 130)):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=S(width), anchor="w")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._paint_scope())

        scope = tk.Frame(content, bg=BG)
        scope.pack(fill="x", padx=S(14), pady=(S(6), 0))
        self.sel_only_var = tk.BooleanVar(
            value=bool(self.prefs.get("selected_rows_only", True)))
        tk.Checkbutton(scope, text="Only rows selected above", variable=self.sel_only_var,
                       command=self._on_scope_change, bg=BG, fg=FG, selectcolor=PANEL,
                       activebackground=BG, activeforeground=FG, font=FONT(9),
                       highlightthickness=0).pack(side="left")
        self.scope_label = tk.Label(scope, text="", bg=BG, fg=SUB, font=FONT(8))
        self.scope_label.pack(side="left", padx=(S(8), 0))
        make_button(scope, "Select all", self.select_all_rows).pack(side="right")
        make_button(scope, "Clear selection",
                    lambda: self.tree.selection_remove(self.tree.selection())
                    ).pack(side="right", padx=(0, S(6)))

        ops = tk.Frame(content, bg=BG)
        ops.pack(fill="x", padx=S(14), pady=(S(12), 0))

        r1 = tk.Frame(ops, bg=BG)
        r1.pack(fill="x", pady=(0, S(8)))
        tk.Label(r1, text="Peak is", bg=BG, fg=SUB, font=FONT(9), width=9,
                 anchor="w").pack(side="left")
        self.mode_var = tk.StringVar(value=self.prefs.get("peak_mode", PEAK_MODES[0]))
        mode_box = ttk.Combobox(r1, textvariable=self.mode_var, values=PEAK_MODES,
                                state="readonly", width=24, font=FONT(9))
        mode_box.pack(side="left")
        mode_box.bind("<<ComboboxSelected>>", lambda e: self.on_mode_change(e))
        make_button(r1, "Move ease peak to playhead", self.do_reshape,
                    primary=True).pack(side="left", padx=(S(10), 0))
        make_button(r1, "Reset ease", self.do_reset).pack(side="left", padx=(S(6), 0))

        r1a = tk.Frame(ops, bg=BG)
        r1a.pack(fill="x", pady=(0, S(8)))
        tk.Label(r1a, text="", bg=BG, font=FONT(9), width=9).pack(side="left")
        self.fallback_var = tk.BooleanVar(value=bool(self.prefs.get("fallback", True)))
        tk.Checkbutton(r1a, text="If the handles can't reach, move the keyframes "
                                 "the rest of the way",
                       variable=self.fallback_var, command=self._save_fallback,
                       bg=BG, fg=FG, selectcolor=PANEL, activebackground=BG,
                       activeforeground=FG, font=FONT(9),
                       highlightthickness=0).pack(side="left")

        r1b = tk.Frame(ops, bg=BG)
        r1b.pack(fill="x", pady=(0, S(8)))
        tk.Label(r1b, text="Safety", bg=BG, fg=SUB, font=FONT(9), width=9,
                 anchor="w").pack(side="left")
        make_button(r1b, "Undo last change", self.do_restore).pack(side="left")
        make_button(r1b, "Dump handles (read-only)",
                    self.do_dump).pack(side="left", padx=(S(6), 0))
        self.backup_label = tk.Label(r1b, text="", bg=BG, fg=SUB, font=FONT(8))
        self.backup_label.pack(side="left", padx=(S(8), 0))

        r2 = tk.Frame(ops, bg=BG)
        r2.pack(fill="x")
        tk.Label(r2, text="Nudge", bg=BG, fg=SUB, font=FONT(9), width=9,
                 anchor="w").pack(side="left")
        make_button(r2, "◀", lambda: self.do_nudge(-1), width=3).pack(side="left")
        self.step_var = tk.StringVar(value=str(self.prefs.get("nudge_step", 1)))
        tk.Entry(r2, textvariable=self.step_var, width=5, bg=PANEL, fg=FG,
                 relief="flat", insertbackground=FG, font=FONT(9), justify="center",
                 highlightthickness=1, highlightbackground=BORDER
                 ).pack(side="left", padx=S(6))
        make_button(r2, "▶", lambda: self.do_nudge(1), width=3).pack(side="left")
        tk.Label(r2, text="frame(s)", bg=BG, fg=SUB,
                 font=FONT(9)).pack(side="left", padx=(S(6), S(14)))
        self.nudge_scope_var = tk.StringVar(
            value=self.prefs.get("nudge_scope", NUDGE_SCOPES[0]))
        scope_box = ttk.Combobox(r2, textvariable=self.nudge_scope_var,
                                 values=NUDGE_SCOPES, state="readonly",
                                 width=24, font=FONT(9))
        scope_box.pack(side="left")
        scope_box.bind("<<ComboboxSelected>>", lambda e: self.on_nudge_scope_change(e))

        tk.Label(content, text="Moving the peak does not move anything: keyframe "
                               "times and values are untouched and only the two "
                               "handles around the playhead's segment are "
                               "redistributed. The total amount of ease is kept, so "
                               "the curve gets lopsided, not sharper. Reset ease puts "
                               "the split back to even.\n"
                               "Fusion exposes selected NODES, not selected "
                               "keyframes — nothing can see a marquee in the Spline "
                               "editor — so the segment acted on is the one under "
                               "the playhead.",
                 bg=BG, fg=SUB, font=FONT(8), anchor="w", justify="left",
                 wraplength=S(600)).pack(fill="x", padx=S(14), pady=(S(10), S(14)))

        self.status = tk.Label(bottom, text="", bg=PANEL2, fg=SUB, anchor="w",
                               font=FONT(8), padx=S(12), pady=S(5))
        self.status.pack(side="left", fill="x", expand=True)
        tk.Label(bottom, text="UI scale", bg=PANEL2, fg=SUB,
                 font=FONT(8)).pack(side="left", padx=(0, S(6)))
        self.scale_var = tk.StringVar(value=self.prefs.get("ui_scale", "Auto"))
        box = ttk.Combobox(bottom, textvariable=self.scale_var, values=SCALE_CHOICES,
                           state="readonly", width=6, font=FONT(8))
        box.pack(side="left", padx=(0, S(10)), pady=S(3))
        box.bind("<<ComboboxSelected>>", self.on_scale_change)

    def say(self, msg, error=False):
        self.status.config(text=msg, fg="#e07070" if error else SUB)

    def _paint_topmost(self):
        on = bool(self.prefs.get("always_on_top", False))
        self.top_btn.config(bg=ACCENT if on else BTN,
                            fg="#ffffff" if on else FG,
                            activebackground=ACCENT_HOVER if on else BTN_HOVER)

    def toggle_topmost(self):
        on = not bool(self.prefs.get("always_on_top", False))
        self.prefs["always_on_top"] = on
        save_prefs(self.prefs)
        try:
            self.root.attributes("-topmost", on)
        except Exception:
            pass
        self._paint_topmost()

    def _save_fallback(self):
        self.prefs["fallback"] = bool(self.fallback_var.get())
        save_prefs(self.prefs)

    def on_mode_change(self, event=None):
        if event and getattr(event, "widget", None):
            try:
                event.widget.selection_clear()
            except Exception:
                pass
        self.prefs["peak_mode"] = self.mode_var.get()
        save_prefs(self.prefs)
        self.rescan()

    def on_nudge_scope_change(self, event=None):
        if event and getattr(event, "widget", None):
            try:
                event.widget.selection_clear()
            except Exception:
                pass
        self.prefs["nudge_scope"] = self.nudge_scope_var.get()
        save_prefs(self.prefs)

    def on_scale_change(self, event=None):
        global SCALE
        if event and getattr(event, "widget", None):
            try:
                event.widget.selection_clear()
            except Exception:
                pass
        self.prefs["ui_scale"] = self.scale_var.get()
        save_prefs(self.prefs)
        SCALE = resolve_scale(self.prefs, self.root)
        for child in self.root.winfo_children():
            child.destroy()
        self.root.minsize(S(640), S(360))
        self._style()
        self._build()
        self.rescan()
        try:
            self.root.attributes("-topmost", bool(self.prefs.get("always_on_top", False)))
        except Exception:
            pass

    # -- scanning ---------------------------------------------------------
    def rescan(self):
        try:
            self._rescan()
        except Exception as e:
            traceback.print_exc()
            self.say(f"Scan failed: {e}", True)

    def _rescan(self):
        keep = set(self.tree.selection()) if self.tree.get_children() else set()
        self.tree.delete(*self.tree.get_children())
        self.rows = []

        comp = current_comp()
        if not comp:
            self.comp_label.config(text="No composition open")
            self.say("Open a Fusion composition and select the nodes to work on.", True)
            self._paint_scope()
            return

        try:
            name = comp.GetAttrs()["COMPS_Name"]
        except Exception:
            name = "Composition"
        self.comp_label.config(text=name)
        frame = playhead(comp)

        try:
            tools = list((comp.GetToolList(True) or {}).values())
        except Exception:
            tools = []
        if not tools:
            self.info.config(text=f"playhead at frame {frame:g}")
            self.say("No nodes selected — select the nodes whose easing you "
                     "want to reshape.", True)
            self._paint_scope()
            return

        mode = self.mode_var.get()
        for tool in tools:
            try:
                tool_name = tool.Name
            except Exception:
                continue
            for input_id, input_name, spline in animated_inputs(tool):
                try:
                    spline_name = str(spline.Name)
                except Exception:
                    spline_name = input_id
                for seg in segments_of(spline):
                    # Only the segment the playhead sits in can be reshaped to
                    # put its peak there, so that is the only one listed.
                    if not (seg["t0"] <= frame <= seg["t1"]):
                        continue
                    bkey = baseline_key(name, spline_name, seg)
                    attach_baseline(seg, self.baselines, bkey)
                    live = current_controls(seg)
                    self.rows.append({
                        "tool_name": tool_name,
                        "input_name": input_name,
                        "spline": spline,
                        "spline_name": spline_name,
                        "comp_name": name,
                        "bkey": bkey,
                        "seg": seg,
                        "linear": live is None,
                        "peak": peak_time(seg, live, mode),
                        "speed": peak_speed(seg, live),
                        "iid": f"{tool_name}.{input_id}.{seg['t0']:g}",
                    })

        self.info.config(text=f"{len(tools)} node(s) selected · "
                              f"{len(self.rows)} segment(s) under the playhead · "
                              f"frame {frame:g}")

        seen = set()
        for row in self.rows:
            iid = row["iid"]
            while iid in seen:
                iid += "_"
            seen.add(iid)
            row["iid"] = iid
            if row["linear"]:
                state = "linear — no ease"
            elif row["peak"] is None:
                state = "flat — nothing moves"
            else:
                delta = row["peak"] - frame
                state = "on the playhead" if abs(delta) < 0.5 else f"{delta:+.1f} frames off"
            self.tree.insert("", "end", iid=iid, values=(
                row["tool_name"], row["input_name"],
                f"{row['seg']['t0']:g} → {row['seg']['t1']:g}",
                "—" if row["peak"] is None else f"{row['peak']:.4g}", state))

        try:
            count = len(list_backups())
            self.backup_label.config(
                text=f"{count} snapshot(s) kept" if count else "no snapshots yet")
        except Exception:
            pass

        restore = [r["iid"] for r in self.rows if r["iid"] in keep]
        if restore:
            self.tree.selection_set(restore)

        if not self.rows:
            self.say("No animated segment under the playhead — park the playhead "
                     "between two keyframes of the selected node(s).", True)
        else:
            self.say(f"{len(self.rows)} segment(s) under the playhead at frame {frame:g}.")
        self._paint_scope()

    # -- scope ------------------------------------------------------------
    def _on_scope_change(self):
        self.prefs["selected_rows_only"] = bool(self.sel_only_var.get())
        save_prefs(self.prefs)
        self._paint_scope()

    def select_all_rows(self):
        kids = self.tree.get_children()
        if kids:
            self.tree.selection_set(kids)

    def _scoped(self):
        if not self.rows or not self.sel_only_var.get():
            return list(self.rows)
        chosen = set(self.tree.selection())
        if not chosen:
            return list(self.rows)
        return [r for r in self.rows if r["iid"] in chosen]

    def _paint_scope(self):
        total = len(self.rows)
        n = len(self._scoped())
        if not total:
            text = ""
        elif n == total:
            text = f"acting on all {total} segment(s)"
        else:
            text = f"acting on {n} of {total} segment(s)"
        try:
            self.scope_label.config(text=text)
        except Exception:
            pass

    # -- operations -------------------------------------------------------
    def _workable(self):
        comp = current_comp()
        if not comp:
            self.say("No composition open.", True)
            return None, None
        rows = [r for r in self._scoped() if not r["linear"]]
        if not rows:
            self.say("Nothing to reshape — the segment(s) here are linear, so "
                     "there is no easing to move.", True)
            return None, None
        return comp, rows

    def do_reshape(self):
        comp, rows = self._workable()
        if not comp:
            return
        target = playhead(comp)
        mode = self.mode_var.get()
        print(f"\nMove ease peak to frame {target:g} ({mode.lower()}):")
        if not self._snapshot(rows, "reshape"):
            return

        try:
            comp.StartUndo("Move ease peak to playhead")
        except Exception:
            pass
        done = 0
        approximate = 0
        shifted = 0
        for row in rows:
            seg = row["seg"]
            split, got, exact = solve_split(seg, target, mode)
            if split is None:
                print(f"  {row['tool_name']}.{row['input_name']}: no measurable peak")
                continue

            # Fallback: the handles have run out of room, so close the rest of
            # the gap by re-timing the animation. The whole spline moves as one
            # rigid piece - shifting only this segment's two keys would stretch
            # its neighbours and wreck their easing. Handles do the fine work,
            # keys do the coarse.
            if not exact and self.fallback_var.get():
                # Handles first, keys last, and as few frames as possible.
                # 'got' is the peak with the split already pushed to its
                # limit, so the gap left over from THERE is the smallest shift
                # that can reach the playhead. Measuring from the authored
                # curve instead would preserve the ease shape but move the
                # keys further, which is the wrong trade here.
                residual = int(round(target - got))
                if residual and shift_spline(row["spline"], residual):
                    self._rebase(row, residual)
                    seg = row["seg"]
                    shifted += 1
                    print(f"  {row['tool_name']}.{row['input_name']}: handles "
                          f"maxed out — moved the keys {residual:+d} frame(s)")
                    split, got, exact = solve_split(seg, target, mode)
                    if split is None:
                        continue
            if not apply_split(row["spline"], seg, split):
                print(f"  {row['tool_name']}.{row['input_name']}: FAILED to write")
                continue
            done += 1
            if not exact:
                approximate += 1
            # Report the ease strength either side of the change. Total handle
            # length is held constant, so this should stay in the same
            # ballpark - if it collapses run after run, something is wrong.
            was = row["speed"]
            now = peak_speed(seg, controls(seg, split))
            print(f"  {row['tool_name']}.{row['input_name']}: peak "
                  f"{row['peak'] if row['peak'] is None else round(row['peak'], 2)}"
                  f" -> {round(got, 2)}  (split {split:.3f}, "
                  f"speed {'?' if was is None else round(was, 3)}"
                  f" -> {'?' if now is None else round(now, 3)})"
                  f"{'' if exact else '  [as close as the handles allow]'}")
        try:
            comp.EndUndo(True)
        except Exception:
            pass

        self.rescan()
        note = ""
        if shifted:
            note += f"  {shifted} needed the keyframes moved."
        if approximate:
            note += f"  {approximate} could not reach the playhead exactly."
        self.say(f"Reshaped {done} of {len(rows)} segment(s).{note}")

    def do_reset(self):
        """Put the authored ease back.

        Not 'an even split' - the authored proportion, which for an
        asymmetric ease is not the same thing. This reproduces the handles
        recorded the first time the segment was seen.
        """
        comp, rows = self._workable()
        if not comp:
            return
        print("\nReset ease to the authored shape:")
        if not self._snapshot(rows, "reset"):
            return
        try:
            comp.StartUndo("Reset ease")
        except Exception:
            pass
        done = 0
        for row in rows:
            split = baseline_split(row["seg"])
            if split is None:
                continue
            if apply_split(row["spline"], row["seg"], split):
                done += 1
                print(f"  {row['tool_name']}.{row['input_name']}: "
                      f"reset to split {split:.3f}")
        try:
            comp.EndUndo(True)
        except Exception:
            pass
        self.rescan()
        self.say(f"Reset {done} of {len(rows)} segment(s).")

    def _rebase(self, row, offset):
        """Follow a spline that has just been shifted in time.

        The in-memory segment and the stored baseline both key off absolute
        frames, so they have to move with it - otherwise the next scan would
        record a fresh baseline from the already-reshaped handles and the
        drift this tool was fixed for would come straight back.
        """
        seg = row["seg"]
        seg["t0"] += offset
        seg["t1"] += offset
        for field in ("rh", "lh", "brh", "blh"):
            point = seg.get(field)
            if point:
                seg[field] = (point[0] + offset, point[1])

        entry = self.baselines.pop(row["bkey"], None)
        row["bkey"] = baseline_key(row["comp_name"], row["spline_name"], seg)
        if entry:
            self.baselines[row["bkey"]] = {
                "rh": [entry["rh"][0] + offset, entry["rh"][1]],
                "lh": [entry["lh"][0] + offset, entry["lh"][1]],
            }
        save_baselines(self.baselines)

    def _snapshot(self, rows, label):
        """Snapshot before writing. No snapshot, no write - Fusion's own undo
        has not proved trustworthy for scripted keyframe edits, and this is
        the only way back."""
        saved = write_backup([r["spline"] for r in rows], label)
        print(f"  snapshot: {saved or 'FAILED'}")
        if not saved:
            self.say("Could not write a safety snapshot, so nothing was "
                     "changed.", True)
            return False
        return True

    def do_restore(self):
        """Roll back to the snapshot taken before the last write."""
        comp = current_comp()
        if not comp:
            self.say("No composition open.", True)
            return
        backups = list_backups()
        if not backups:
            self.say("No snapshots to roll back to.", True)
            return
        newest = backups[-1]
        print(f"\nRestoring snapshot {newest}:")
        try:
            comp.StartUndo("Restore keyframe snapshot")
        except Exception:
            pass
        restored, failed = restore_backup(comp, newest)
        try:
            comp.EndUndo(True)
        except Exception:
            pass
        self.rescan()
        if restored and not failed:
            self.say(f"Rolled back {restored} spline(s) from {newest}.")
        else:
            self.say(f"Rolled back {restored} spline(s), {failed} failed — "
                     f"see the console.", bool(failed))

    def do_dump(self):
        """Print the raw keyframe tables. Reads only, writes nothing.

        Use this to check what Fusion actually stores before trusting any
        write - it is how the handle convention gets confirmed rather than
        assumed.
        """
        rows = self._scoped()
        if not rows:
            self.say("Nothing to dump — select some animated nodes.", True)
            return
        seen = set()
        print("\nRaw keyframe tables (no changes made):")
        for row in rows:
            spline = row["spline"]
            try:
                name = str(spline.Name)
            except Exception:
                name = "?"
            if name in seen:
                continue
            seen.add(name)
            print(f"\n  {row['tool_name']}.{row['input_name']}  [{name}]")
            try:
                keys = spline.GetKeyFrames() or {}
            except Exception as e:
                print(f"    GetKeyFrames failed: {e}")
                continue
            for frame in sorted(k for k in keys if isinstance(k, (int, float))):
                print(f"    frame {frame:g}: {keys[frame]}")
        self.say(f"Dumped {len(seen)} spline(s) to the console — nothing changed.")

    def do_nudge(self, direction):
        comp = current_comp()
        if not comp:
            self.say("No composition open.", True)
            return
        try:
            step = int(round(float(self.step_var.get().strip())))
        except ValueError:
            self.say("Nudge step must be a whole number of frames.", True)
            return
        if step <= 0:
            self.say("Nudge step must be 1 or more.", True)
            return
        self.prefs["nudge_step"] = step
        save_prefs(self.prefs)

        rows = self._scoped()
        if not rows:
            self.say("Nothing to nudge — select some animated nodes and rescan.", True)
            return

        # One spline can appear as several rows; nudge each spline once.
        splines = {}
        for row in rows:
            splines.setdefault(f"{row['tool_name']}.{row['input_name']}", row)

        offset = step * direction
        tail_only = self.nudge_scope_var.get() == NUDGE_SCOPES[1]
        from_frame = playhead(comp) if tail_only else None
        where = f" from frame {from_frame:g} onward" if tail_only else ""
        print(f"\nNudge {offset:+d} frame(s){where}:")
        if not self._snapshot(list(splines.values()), "nudge"):
            return

        try:
            comp.StartUndo("Nudge keyframes")
        except Exception:
            pass
        moved = 0
        for label, row in splines.items():
            good = shift_spline(row["spline"], offset, from_frame)
            moved += 1 if good else 0
            print(f"  {label}: {offset:+d} "
                  f"{'' if good else '(no keys in range, or FAILED)'}")
        try:
            comp.EndUndo(True)
        except Exception:
            pass

        self.rescan()
        self.say(f"Nudged {moved} of {len(splines)} parameter(s) by {offset:+d} frame(s).")

    def _on_close(self):
        try:
            self.prefs["geometry"] = self.root.geometry()
            self.prefs["geometry_scale"] = round(SCALE, 3)
            save_prefs(self.prefs)
        except Exception:
            pass
        self.root.destroy()


prefs = load_prefs()
root = tk.Tk()
SCALE = resolve_scale(prefs, root)
app = SplineSync(root)
root.bind("<Escape>", lambda e: app._on_close())
root.lift()
root.attributes("-topmost", True)
if not prefs.get("always_on_top", False):
    root.after(300, lambda: root.attributes("-topmost", False))
root.mainloop()
