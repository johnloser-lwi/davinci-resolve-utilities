"""Shared helpers for the Resolve utilities - NOT a menu entry.

deploy.sh / deploy.ps1 copy this file to <data dir>/lib (see LIB_DIR below),
outside Resolve's Scripts folder, because every .py in there shows up in the
Workspace > Scripts menu. Each tool finds it with the same three lines:

    import os, sys
    sys.path.insert(0, os.path.join(os.getenv("APPDATA") or os.path.expanduser(
        "~/Library/Application Support"), "ResolveUtilities", "lib"))
    import resolve_utils as ru

WHAT IT GIVES THE TOOLS
  - DATA_DIR / data_path(): where prefs, queues and logs live
      Windows  %APPDATA%                                    (as it always was)
      Mac      ~/Library/Application Support/ResolveUtilities
  - RESOLVE_SCRIPTS / SCRIPTS_ROOT / find_script(): where the deployed copies are
  - bootstrap(): makes the Tk tools work on a Mac (below)
  - message() / ask_yes_no(): a dialog that works with or without Tk
  - Mac-safe UI bits: Button, font(), detect_scale(), wheel_units()
  - read_json() / write_json(): prefs I/O; writes are atomic

WHY THE MAC NEEDS bootstrap()
  Resolve 21 on macOS runs scripts in its own bundled Python, which has no Tk at
  all, and even a Python that had it couldn't open Tk windows from Resolve's
  script thread (macOS only allows windows on an app's main thread). So on a Mac
  a Tk tool re-opens itself in a separate Python that has Tk, and that process
  drives Resolve over Resolve's scripting connection - the same API, so the tool
  code doesn't change. Needs, once:
    - a Python with Tk: `brew install python-tk@3.15` or the python.org installer
    - Resolve > Preferences > System > General > External scripting using: Local
  Its print() output goes to Resolve's Console as before, and to
  <DATA_DIR>/logs/<tool>.log.
"""
import ctypes
import importlib.util
import json
import os
import subprocess
import sys

IS_MAC = sys.platform == "darwin"
IS_WIN = os.name == "nt"

# ---------------------------------------------------------------- paths

if IS_WIN:
    DATA_DIR = os.environ.get("APPDATA") or os.path.expanduser(r"~\AppData\Roaming")
    LIB_DIR = os.path.join(DATA_DIR, "ResolveUtilities", "lib")
    RESOLVE_SCRIPTS = os.path.join(DATA_DIR, "Blackmagic Design", "DaVinci Resolve",
                                   "Support", "Fusion", "Scripts", "Utility")
elif IS_MAC:
    _SUPPORT = os.path.expanduser("~/Library/Application Support")
    DATA_DIR = os.path.join(_SUPPORT, "ResolveUtilities")
    LIB_DIR = os.path.join(DATA_DIR, "lib")
    RESOLVE_SCRIPTS = os.path.join(_SUPPORT, "Blackmagic Design", "DaVinci Resolve",
                                   "Fusion", "Scripts", "Utility")
else:
    DATA_DIR = os.path.expanduser("~/.config/ResolveUtilities")
    LIB_DIR = os.path.join(DATA_DIR, "lib")
    RESOLVE_SCRIPTS = os.path.expanduser(
        "~/.local/share/DaVinciResolve/Fusion/Scripts/Utility")

SCRIPTS_ROOT = os.path.join(RESOLVE_SCRIPTS, "John")      # where deploy puts the tools
LOG_DIR = os.path.join(DATA_DIR, "logs")

try:
    os.makedirs(DATA_DIR, exist_ok=True)
except OSError:
    pass


def data_path(*parts):
    """A file or folder in the prefs folder, e.g. data_path("align_clips_prefs.json")."""
    return os.path.join(DATA_DIR, *parts)


def find_script(g, filename, folder):
    """Path of a sibling tool, e.g. find_script(globals(), "01_x.py", "FxLink").

    Next to the caller first (works from the repo too), then the deployed
    folder, then anywhere under Resolve's Scripts folder (in case it was moved).
    """
    import glob
    candidates = []
    here = g.get("__file__")
    if here:
        here = os.path.dirname(os.path.abspath(here))
        candidates.append(os.path.join(here, filename))                         # same folder
        candidates.append(os.path.join(os.path.dirname(here), folder, filename))  # a sibling tool folder
    candidates.append(os.path.join(SCRIPTS_ROOT, folder, filename))
    stem = filename.split("_", 1)[-1]          # "01_x.py" -> "x.py": survives renumbering
    candidates += glob.glob(os.path.join(RESOLVE_SCRIPTS, "**", "*" + stem), recursive=True)
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def load_library(g, filename, folder, flag, module_name):
    """exec() a sibling tool as a library and return its namespace.

    `flag` is the global the tool checks to skip its own UI, e.g.
    "FXLINK_AS_LIBRARY". Raises RuntimeError if the tool can't be found.
    """
    path = find_script(g, filename, folder)
    if not path:
        raise RuntimeError(f"Couldn't find {filename}. Run deploy.sh (Mac) / deploy.ps1 (Windows).")
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    ns = {"bmd": g.get("bmd"), "__name__": module_name, "__file__": path, flag: True}
    exec(compile(source, path, "exec"), ns)
    return ns


def read_json(path, default=None):
    """Parsed JSON from path, or `default` if it's missing or unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path, data):
    """Write via a temp file and rename, so a crash mid-write can't leave a
    half-written prefs or queue file behind. Returns True on success."""
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
        return True
    except Exception as e:
        print(f"Could not save {os.path.basename(path)}: {e}")
        return False


def open_path(path):
    """Show a file or folder in Finder / Explorer."""
    try:
        if IS_WIN:
            os.startfile(path)
        elif IS_MAC:
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        print(f"Could not open {path}: {e}")


# ---------------------------------------------------------------- Windows DPI

# Tell Windows we handle our own DPI, so the panels aren't tiny on a 4K screen.
# Must happen before the first Tk window, which importing this module guarantees.
if IS_WIN:
    for _mod, _fn, _arg in (("shcore", "SetProcessDpiAwareness", 2),
                            ("shcore", "SetProcessDpiAwareness", 1),
                            ("user32", "SetProcessDPIAware", None)):
        try:
            _f = getattr(getattr(ctypes.windll, _mod), _fn)
            _f(_arg) if _arg is not None else _f()
            break
        except Exception:
            continue


# ---------------------------------------------------------------- dialogs without Tk

def has_tk():
    return importlib.util.find_spec("_tkinter") is not None


def _osascript_text(text):
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def osascript(lines, timeout=None):
    """Run AppleScript lines; returns (exit code, stdout)."""
    args = ["osascript"]
    for line in lines:
        args += ["-e", line]
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return out.returncode, out.stdout.strip()
    except Exception:
        return 1, ""


def message(title, text, kind="info"):
    """A blocking message box. kind: "info", "warning" or "error"."""
    print(f"{title}: {text}")
    if IS_MAC and not (has_tk() and "_RESOLVE_UTILS_RUNNER" in os.environ):
        icon = {"info": "informational", "warning": "warning", "error": "critical"}[kind]
        osascript(["activate",
                    f"display alert {_osascript_text(title)} message {_osascript_text(text)} "
                    f"as {icon} buttons {{\"OK\"}} default button \"OK\""])
        return
    try:
        from tkinter import Tk, messagebox
        root = Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        {"info": messagebox.showinfo, "warning": messagebox.showwarning,
         "error": messagebox.showerror}[kind](title, text, **dialog_parent(root))
        root.destroy()
    except Exception:
        pass


def dialog_parent(window=None):
    """parent= for a Tk messagebox / file dialog:  messagebox.showinfo(..., **ru.dialog_parent(root))

    On a Mac a dialog with a parent opens as a sheet ATTACHED to that window,
    so a hidden (withdrawn or minimized) parent leaves it invisible - or stuck
    where it can't be moved - while the tool waits for a click nobody can make.
    There the parent is only passed when it's on screen; otherwise the dialog
    is a normal free-standing window. Also brings this process to the front,
    since a tool running outside Resolve otherwise opens dialogs behind it.
    """
    if window is None:
        activate_app()
        return {}
    if not IS_MAC:
        return {"parent": window}         # Windows: a topmost parent keeps it above Resolve
    activate_app()
    try:
        if window.winfo_viewable():
            return {"parent": window}
    except Exception:
        pass
    return {}


def ask_directory(fusion=None, initial=None, title="Choose a folder"):
    """A folder picker; returns the path or None.

    In Resolve this is Fusion's own picker, as before. Run outside Resolve (the
    Mac panels) Fusion's picker would open inside Resolve, behind this window,
    so the native picker is used instead.
    """
    if "_RESOLVE_UTILS_RUNNER" in os.environ or fusion is None:
        import tkinter as tk
        from tkinter import filedialog
        temp = None
        parent = getattr(tk, "_default_root", None)
        if parent is None:
            temp = parent = tk.Tk()
            parent.withdraw()
        # A hidden parent would strand a Mac picker - see dialog_parent().
        options = dialog_parent(parent)
        try:
            start = initial if initial and os.path.isdir(initial) else os.path.expanduser("~")
            path = filedialog.askdirectory(initialdir=start, title=title, mustexist=False,
                                           **options)
        finally:
            if temp is not None:
                temp.destroy()
        return path or None
    return fusion.RequestDir(initial + os.sep) if initial else fusion.RequestDir()


def ask_yes_no(title, text):
    """A blocking Yes / No question. True for Yes."""
    if IS_MAC and not (has_tk() and "_RESOLVE_UTILS_RUNNER" in os.environ):
        code, out = osascript(["activate",
                                f"display dialog {_osascript_text(text)} with title "
                                f"{_osascript_text(title)} buttons {{\"No\", \"Yes\"}} "
                                f"default button \"Yes\" with icon caution"])
        return code == 0 and out.endswith("Yes")
    from tkinter import Tk, messagebox
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    answer = messagebox.askyesno(title, text, **dialog_parent(root))
    root.destroy()
    return answer


# ---------------------------------------------------------------- UI helpers

if IS_MAC:
    UI_FONT, MONO_FONT = "Helvetica Neue", "Menlo"
    # Tk on a Mac counts points at 72 per inch, Windows at 96. Without this every
    # size written for Windows comes out a quarter smaller.
    FONT_SCALE = 96.0 / 72.0
elif IS_WIN:
    UI_FONT, MONO_FONT, FONT_SCALE = "Segoe UI", "Consolas", 1.0
else:
    UI_FONT, MONO_FONT, FONT_SCALE = "DejaVu Sans", "DejaVu Sans Mono", 1.0


def font(size, weight=None, scale=1.0, mono=False):
    """A Tk font tuple sized the same on every OS. scale is the panel's UI scale."""
    pts = max(6, int(round(size * scale * FONT_SCALE)))
    family = MONO_FONT if mono else UI_FONT
    return (family, pts, weight) if weight else (family, pts)


def detect_scale(root):
    """The panel's UI scale. A Mac's Retina scaling is done by macOS, so it's 1."""
    if IS_MAC:
        return 1.0
    try:
        dpi = root.winfo_fpixels("1i")
    except Exception:
        dpi = 96.0
    scale = dpi / 96.0
    if scale < 1.05:
        # A 4K panel left at 100% Windows scaling still reports 96 DPI
        try:
            width = root.winfo_screenwidth()
        except Exception:
            width = 1920
        if width >= 3400:
            scale = 1.5
        elif width >= 2800:
            scale = 1.25
    return max(1.0, min(scale, 3.0))


def wheel_units(event):
    """Scroll units for a <MouseWheel> event - Windows sends multiples of 120,
    a Mac sends small numbers (and a trackpad sends many of them)."""
    d = event.delta
    if abs(d) >= 120:
        return int(-d / 120)
    return -1 if d > 0 else (1 if d < 0 else 0)


def bind_delete(widget, callback):
    """Bind the Delete key - which on a Mac keyboard is BackSpace."""
    widget.bind("<Delete>", callback)
    if IS_MAC:
        widget.bind("<BackSpace>", callback)


def _make_button_class():
    import tkinter as tk
    if not IS_MAC:
        return tk.Button

    class Button(tk.Label):
        """tk.Button look-alike for macOS, where a native button ignores bg/fg
        and the dark panels would get white buttons with unreadable text.

        Takes the same options; `command` runs on a click released inside it.
        """
        def __init__(self, master=None, cnf=None, **kw):
            kw = {**(cnf or {}), **kw}
            self._command = kw.pop("command", None)
            for k in ("overrelief", "default", "repeatdelay", "repeatinterval"):
                kw.pop(k, None)
            super().__init__(master, **kw)
            self._down = False
            self.bind("<ButtonPress-1>", self._press, add="+")
            self.bind("<ButtonRelease-1>", self._release, add="+")

        def _enabled(self):
            return str(super().cget("state")) != "disabled"

        def _press(self, _event):
            self._down = self._enabled()

        def _release(self, event):
            inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
            if self._down and inside:
                self.invoke()
            self._down = False

        def invoke(self):
            if self._enabled() and self._command:
                return self._command()

        def configure(self, cnf=None, **kw):
            if cnf is None and not kw:
                return super().configure()
            if isinstance(cnf, str):
                return super().configure(cnf)
            kw = {**(cnf or {}), **kw}
            if "command" in kw:
                self._command = kw.pop("command")
            for k in ("overrelief", "default", "repeatdelay", "repeatinterval"):
                kw.pop(k, None)
            if kw:
                super().configure(**kw)

        config = configure

        def cget(self, key):
            return self._command if key == "command" else super().cget(key)

        def __getitem__(self, key):
            return self.cget(key)

        def __setitem__(self, key, value):
            self.configure({key: value})

        def flash(self):
            pass

    return Button


class _LazyButton:
    """ru.Button(...) without importing tkinter until a panel actually needs it
    (so Tk-free tools can import this module inside Resolve's Python).

    On a Mac only STYLED buttons (given a colour or a relief) become the
    label-based look-alike; a plain OK / Cancel stays a native macOS button.
    """
    _cls = None

    def __call__(self, *args, **kwargs):
        if _LazyButton._cls is None:
            _LazyButton._cls = _make_button_class()
        styled = any(k in kwargs for k in ("bg", "background", "relief", "activebackground"))
        if IS_MAC and not styled:
            import tkinter as tk
            return tk.Button(*args, **kwargs)
        return _LazyButton._cls(*args, **kwargs)


Button = _LazyButton()


# ---------------------------------------------------------------- Mac: run Tk tools outside Resolve

class HandedOff(Exception):
    """Raised inside Resolve once a Tk tool has been re-opened in its own
    Python, to stop the copy inside Resolve. Not an error."""


_PREV_HOOK = sys.excepthook


def _quiet_handoff_hook(kind, value, tb):
    if issubclass(kind, HandedOff):
        return
    _PREV_HOOK(kind, value, tb)


PYTHON_CACHE = os.path.join(DATA_DIR, "python_for_tk.json")


def _python_candidates():
    import glob
    seen, out = set(), []

    def add(path):
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            real = os.path.realpath(path)
            if real not in seen:
                seen.add(real)
                out.append(path)

    add(os.environ.get("RESOLVE_UTILS_PYTHON"))
    # python.org installers bundle their own, current Tk - the best choice.
    for p in sorted(glob.glob("/Library/Frameworks/Python.framework/Versions/3.*/bin/python3"),
                    reverse=True):
        add(p)
    for prefix in ("/opt/homebrew", "/usr/local"):
        add(os.path.join(prefix, "bin", "python3"))
        for p in sorted(glob.glob(os.path.join(prefix, "opt", "python@3.*", "bin", "python3.*")),
                        reverse=True):
            if not p.endswith("-config"):
                add(p)
    # Apple's own Python (/usr/bin/python3) is left out on purpose: its Tk 8.5
    # hangs on macOS 26 as soon as a window is drawn.
    return out


def _clean_env():
    env = dict(os.environ)
    # Resolve points these at its own bundled Python; a different Python must
    # not inherit them or it loads the wrong standard library.
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "PYTHONSTARTUP",
                "__PYVENV_LAUNCHER__", "PYTHONNOUSERSITE"):
        env.pop(key, None)
    return env


def find_tk_python():
    """A Python 3 that has Tk, or None. The answer is cached in DATA_DIR."""
    cached = read_json(PYTHON_CACHE, {})
    path = cached.get("path")
    if path and os.path.isfile(path):
        try:
            if os.path.getmtime(path) == cached.get("mtime"):
                return path
        except OSError:
            pass
    probe = "import sys, tkinter; sys.stdout.write(str(tkinter.TkVersion))"
    for candidate in _python_candidates():
        try:
            out = subprocess.run([candidate, "-c", probe], capture_output=True, text=True,
                                 timeout=15, env=_clean_env())
            version = float(out.stdout.strip()) if out.returncode == 0 else 0.0
        except Exception:
            continue
        if version >= 8.6:                  # 8.5 (Apple's) hangs on current macOS
            write_json(PYTHON_CACHE, {"path": candidate, "tk": version,
                                      "mtime": os.path.getmtime(candidate)})
            return candidate
    return None


NO_PYTHON_HELP = (
    "This tool's window needs a Python with Tk 8.6 or newer, and Resolve's own "
    "Python on a Mac doesn't include Tk at all.\n\n"
    "Install one (either works):\n"
    "  -  Terminal:  brew install python-tk@3.15\n"
    "  -  The installer from python.org/downloads\n\n"
    "Then run the tool again.")

NO_CONNECTION_HELP = (
    "Couldn't connect to DaVinci Resolve.\n\n"
    "Tools with a window run in their own process on a Mac and talk to Resolve "
    "over its scripting connection. Turn it on in Resolve:\n"
    "  Preferences > System > General > External scripting using: Local\n\n"
    "Then run the tool again.")


def bootstrap(g, rel_path):
    """Call at the top of every Tk tool, before `bmd` is used.

    g is the tool's globals(); rel_path its path inside the deployed folder,
    e.g. "Utility/05_align_clips.py" (used when Resolve gives no __file__).

      - Windows, or Resolve with a Tk-capable Python: returns, nothing changes.
      - Run from a terminal: provides `bmd` (Resolve's scripting connection).
      - Inside Resolve on a Mac: re-opens the tool in a Tk-capable Python and
        raises HandedOff to end this copy. Resolve's Console just reports it.
    """
    if "bmd" not in g:
        g["bmd"] = ExternalBmd()
        return
    if has_tk():
        return

    name = os.path.basename(rel_path)
    path = g.get("__file__") or os.path.join(SCRIPTS_ROOT, *rel_path.split("/"))
    if not os.path.isfile(path):
        message(name, f"Can't find the tool itself at:\n{path}\n\nRun deploy.sh again.", "error")
        raise HandedOff(name)

    python = find_tk_python()
    if not python:
        message(name, NO_PYTHON_HELP, "warning")
        sys.excepthook = _quiet_handoff_hook
        raise HandedOff(f"{name}: no Python with Tk installed")

    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, os.path.splitext(name)[0] + ".log")
    env = _clean_env()
    env["_RESOLVE_UTILS_RUNNER"] = "1"
    runner = os.path.abspath(__file__)
    with open(log_path, "w", encoding="utf-8") as log:
        subprocess.Popen([python, "-u", runner, "run", path], env=env,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                         close_fds=True, start_new_session=True,
                         cwd=os.path.dirname(path))
    print(f"{name}: opened in its own window (log: {log_path})")
    sys.excepthook = _quiet_handoff_hook
    raise HandedOff(f"{name} opened in its own window - this is not an error.")


# ---------------------------------------------------------------- the external side

_DVR_MODULES = {
    "darwin": "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
    "win32": os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                          "Blackmagic Design", "DaVinci Resolve", "Support",
                          "Developer", "Scripting", "Modules"),
}


def _dvr():
    """Resolve's DaVinciResolveScript module (the external scripting API)."""
    module = sys.modules.get("DaVinciResolveScript")
    if module:
        return module
    folder = os.environ.get("RESOLVE_SCRIPT_API")
    folder = os.path.join(folder, "Modules") if folder else _DVR_MODULES.get(sys.platform)
    if folder and folder not in sys.path:
        sys.path.append(folder)
    import DaVinciResolveScript
    return DaVinciResolveScript


class ExternalBmd:
    """Stands in for Resolve's `bmd` when a tool runs outside Resolve."""

    def __init__(self):
        self._apps = {}

    def scriptapp(self, name, *args):
        app = self._apps.get(name)
        if app is not None:
            return app
        dvr = _dvr()
        app = dvr.scriptapp(name, *args)
        if app is None and name == "Fusion":
            resolve = dvr.scriptapp("Resolve")
            app = resolve.Fusion() if resolve else None
        if app is None:
            if "_RESOLVE_UTILS_RUNNER" in os.environ:
                message("DaVinci Resolve", NO_CONNECTION_HELP, "warning")
            raise RuntimeError(NO_CONNECTION_HELP)
        self._apps[name] = app
        return app

    def quiet_fusion(self):
        """Fusion, or None - no dialogs (used for console forwarding)."""
        try:
            app = self._apps.get("Fusion") or _dvr().scriptapp("Fusion")
            if app is None:
                resolve = _dvr().scriptapp("Resolve")
                app = resolve.Fusion() if resolve else None
            return app
        except Exception:
            return None

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(_dvr(), name)


class _ConsoleTee:
    """print() in the external process: to the log file, and on to Resolve's
    Console (via Fusion's Lua print) so it shows up where it always did."""

    def __init__(self, stream, bmd):
        self.stream = stream
        self.bmd = bmd
        self.buffer = ""
        self.fusion = None
        self.forward = True

    def write(self, text):
        try:
            self.stream.write(text)
        except Exception:
            pass
        if not self.forward:
            return len(text)
        self.buffer += text
        if "\n" in self.buffer:
            chunk, self.buffer = self.buffer.rsplit("\n", 1)
            self._send(chunk)
        return len(text)

    def _send(self, chunk):
        try:
            if self.fusion is None:
                self.fusion = self.bmd.quiet_fusion()
                if self.fusion is None:
                    self.forward = False
                    return
            level = 0
            while ("]" + "=" * level + "]") in chunk:
                level += 1
            eq = "=" * level
            self.fusion.Execute(f"print([{eq}[{chunk}]{eq}])")
        except Exception:
            self.forward = False              # no console - the log still has it

    def flush(self):
        if self.buffer and self.forward:
            chunk, self.buffer = self.buffer, ""
            self._send(chunk)
        try:
            self.stream.flush()
        except Exception:
            pass

    def isatty(self):
        return False

    def fileno(self):
        return self.stream.fileno()


def activate_app():
    """Bring this Python to the front - it starts behind Resolve, which
    launched it. Best effort: macOS may decline, and the panels' -topmost still
    keeps them visible."""
    if not IS_MAC:
        return
    try:
        objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
        send = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(address)
        send_bool = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool)(address)
        app = send(objc.objc_getClass(b"NSApplication"), objc.sel_registerName(b"sharedApplication"))
        if app:
            send_bool(app, objc.sel_registerName(b"activateIgnoringOtherApps:"), True)
    except Exception:
        pass


def _patch_tk():
    """Every Tk root the tool makes comes to the front once it is drawn."""
    import tkinter
    original = tkinter.Tk.__init__

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)

        def front():
            activate_app()
            try:
                self.lift()
                self.focus_force()
            except Exception:
                pass
        self.after(50, front)

    tkinter.Tk.__init__ = init


def run(path):
    """The external process: run one tool with `bmd` connected to Resolve."""
    import builtins
    import traceback
    os.environ["_RESOLVE_UTILS_RUNNER"] = "1"
    bmd = ExternalBmd()
    sys.stdout = _ConsoleTee(sys.stdout, bmd)
    sys.stderr = _ConsoleTee(sys.stderr, bmd)
    # The tool imports this module by name; hand it this copy.
    sys.modules["resolve_utils"] = sys.modules[__name__]
    if IS_MAC:
        _patch_tk()
    g = {"__name__": "__main__", "__file__": path, "__builtins__": builtins, "bmd": bmd}
    try:
        with open(path, "r", encoding="utf-8") as f:
            code = compile(f.read(), path, "exec")
        exec(code, g)
    except HandedOff:
        pass
    except SystemExit:
        pass
    except Exception:
        traceback.print_exc()
    finally:
        sys.stdout.flush()
        sys.stderr.flush()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "run":
        run(sys.argv[2])
    elif len(sys.argv) == 2 and sys.argv[1] == "which-python":
        print(find_tk_python() or "none found")
    else:
        print("usage: resolve_utils.py run <tool.py> | which-python")
