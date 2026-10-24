import ctypes
import os
import re
import shutil
import struct
import time
import zlib
from datetime import datetime
from tkinter import Tk, messagebox

# Paste Clipboard Image - put whatever image is on the clipboard onto the
# timeline at the playhead.
#
# Right-click an image on a website > Copy Image, then run this. The image is
# saved to <project media location>\..\Images - the Images folder the project
# template keeps beside the media folder, which other tools use too - imported
# into an
# "Images" bin, and dropped at the playhead on the first free track ABOVE
# everything already there, so it shows on top.
#
# Silent when it works - the image appearing is the confirmation. A message
# only pops up if something goes wrong. Bind it to a hotkey and it behaves like
# a paste.
#
# WHAT IT CAN READ, best first:
#   1. A copied FILE (Ctrl+C on an image in Explorer)  - the original, untouched
#   2. PNG data   - what browsers put on the clipboard; lossless, keeps alpha
#   3. A bitmap   - screenshots and older apps; converted to PNG here, with no
#                   extra libraries, so it works inside Resolve's Python
#
# Copying an image's ADDRESS (a URL as text) is not supported - that would mean
# downloading from the internet on your behalf. Use Copy Image instead.

FOLDER_NAME = "Images"            # matched case-insensitively against what exists
BIN_NAME = "Images"
DEFAULT_STILL_SECONDS = 5         # only used if Resolve won't say how long a still is
IMPORTABLE = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif",
              ".exr", ".dpx", ".tga", ".psd", ".heic")


class PasteError(Exception):
    """A failure worth showing the user, with a message written for them."""


def fail(message):
    print(f"Paste Clipboard Image: {message}")
    try:
        root = Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showwarning("Paste Clipboard Image", message, parent=root)
        root.destroy()
    except Exception:
        pass


# --------------------------------------------------------------------------
# Windows clipboard
# --------------------------------------------------------------------------

CF_DIB = 8
CF_HDROP = 15
CF_DIBV5 = 17

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_shell32 = ctypes.windll.shell32

# Handles and pointers are 64-bit. Without explicit types ctypes truncates them
# to 32 bits, and the failure looks like "the clipboard is empty".
_user32.GetClipboardData.restype = ctypes.c_void_p
_user32.RegisterClipboardFormatW.argtypes = [ctypes.c_wchar_p]
_user32.RegisterClipboardFormatW.restype = ctypes.c_uint
_user32.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]
_kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
_kernel32.GlobalSize.restype = ctypes.c_size_t
_shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                    ctypes.c_wchar_p, ctypes.c_uint]
_shell32.DragQueryFileW.restype = ctypes.c_uint


def _open_clipboard(attempts=20):
    for _ in range(attempts):
        if _user32.OpenClipboard(0):
            return True
        time.sleep(0.05)
    return False


def _read_bytes(fmt):
    handle = _user32.GetClipboardData(fmt)
    if not handle:
        return None
    ptr = _kernel32.GlobalLock(handle)
    if not ptr:
        return None
    try:
        return ctypes.string_at(ptr, _kernel32.GlobalSize(handle))
    finally:
        _kernel32.GlobalUnlock(handle)


def _read_files():
    handle = _user32.GetClipboardData(CF_HDROP)
    if not handle:
        return []
    count = _shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
    paths = []
    for i in range(count):
        length = _shell32.DragQueryFileW(handle, i, None, 0)
        buffer = ctypes.create_unicode_buffer(length + 1)
        _shell32.DragQueryFileW(handle, i, buffer, length + 1)
        paths.append(buffer.value)
    return paths


def clipboard_image():
    """What image the clipboard holds, as (kind, payload).

    kind is "file" (a path), "png" (PNG bytes) or "dib" (bitmap bytes).
    Raises PasteError when there is nothing usable.
    """
    png_formats = [_user32.RegisterClipboardFormatW(name)
                   for name in ("PNG", "image/png")]

    if not _open_clipboard():
        raise PasteError("Another program is holding the clipboard. Try again.")
    try:
        # A copied file is the original, so it beats any bitmap rendering of it.
        if _user32.IsClipboardFormatAvailable(CF_HDROP):
            images = [p for p in _read_files()
                      if os.path.splitext(p)[1].lower() in IMPORTABLE]
            if images:
                return "file", images[0]

        for fmt in png_formats:
            if fmt and _user32.IsClipboardFormatAvailable(fmt):
                data = _read_bytes(fmt)
                if data and data.startswith(b"\x89PNG"):
                    return "png", data

        for fmt in (CF_DIBV5, CF_DIB):
            if _user32.IsClipboardFormatAvailable(fmt):
                data = _read_bytes(fmt)
                if data:
                    return "dib", data
    finally:
        _user32.CloseClipboard()

    raise PasteError("There's no image on the clipboard.\n\n"
                     "In your browser, right-click the image and choose "
                     "'Copy Image' (not 'Copy Image Address').")


# --------------------------------------------------------------------------
# bitmap -> PNG, with no third-party libraries
#
# Resolve runs this with plain Python, and Pillow is not guaranteed to be
# installed there, so the conversion is done by hand. Only the bitmap layouts
# that actually turn up on a clipboard are handled: 32-bit and 24-bit.
# --------------------------------------------------------------------------

BI_RGB, BI_BITFIELDS, BI_ALPHABITFIELDS = 0, 3, 6
STANDARD_MASKS = (0x00FF0000, 0x0000FF00, 0x000000FF)


def _png(width, height, rgba_rows):
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + row for row in rgba_rows)      # filter 0 per row
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))


def dib_to_png(dib):
    if len(dib) < 40:
        raise PasteError("The clipboard bitmap is damaged (too short).")
    header_size = struct.unpack_from("<I", dib, 0)[0]
    width, height = struct.unpack_from("<ii", dib, 4)
    bitcount = struct.unpack_from("<H", dib, 14)[0]
    compression = struct.unpack_from("<I", dib, 16)[0]
    colours_used = struct.unpack_from("<I", dib, 32)[0]

    if bitcount not in (24, 32):
        raise PasteError(f"Unsupported clipboard bitmap ({bitcount}-bit). "
                         "Copy it as a file or from a browser instead.")
    if compression not in (BI_RGB, BI_BITFIELDS, BI_ALPHABITFIELDS):
        raise PasteError("The clipboard bitmap is compressed in a way this "
                         "can't read. Copy it as a file instead.")

    # Colour masks: inside a V4/V5 header, or tacked on after a plain one.
    masks = STANDARD_MASKS + (0,)
    offset = header_size
    if compression in (BI_BITFIELDS, BI_ALPHABITFIELDS):
        if header_size >= 56:
            masks = struct.unpack_from("<IIII", dib, 40)
        else:
            count = 4 if compression == BI_ALPHABITFIELDS else 3
            values = struct.unpack_from("<" + "I" * count, dib, header_size)
            masks = tuple(values) + ((0,) if count == 3 else ())
            offset += 4 * count
    elif header_size >= 56:
        masks = struct.unpack_from("<IIII", dib, 40)
    offset += colours_used * 4

    w, h = width, abs(height)
    stride = ((w * bitcount + 31) // 32) * 4
    top_down = height < 0
    pixels = dib[offset:offset + stride * h]
    if len(pixels) < stride * h:
        raise PasteError("The clipboard bitmap is damaged (truncated).")

    def source_row(y):
        y = y if top_down else h - 1 - y
        return pixels[y * stride:y * stride + stride]

    opaque_alpha = b"\xff" * w
    rows = []

    if bitcount == 24:
        for y in range(h):
            px = source_row(y)[:w * 3]
            out = bytearray(w * 4)
            out[0::4], out[1::4], out[2::4] = px[2::3], px[1::3], px[0::3]
            out[3::4] = opaque_alpha
            rows.append(bytes(out))
        return _png(w, h, rows)

    # 32-bit. Many apps leave the fourth byte at zero rather than meaning
    # "fully transparent", so an all-zero alpha channel is read as opaque -
    # otherwise a perfectly normal copied image would paste in invisible.
    alpha_mask = masks[3] if len(masks) > 3 else 0
    alpha_in_data = pixels[3::4].count(0) != len(pixels[3::4])
    use_alpha = alpha_in_data and (alpha_mask in (0, 0xFF000000))

    if tuple(masks[:3]) == STANDARD_MASKS:
        for y in range(h):
            px = source_row(y)[:w * 4]
            out = bytearray(w * 4)
            out[0::4], out[1::4], out[2::4] = px[2::4], px[1::4], px[0::4]
            out[3::4] = px[3::4] if use_alpha else opaque_alpha
            rows.append(bytes(out))
        return _png(w, h, rows)

    # Unusual mask layout: slow, general path. Rare on a clipboard.
    def channel(value, mask):
        if not mask:
            return 255
        shift = (mask & -mask).bit_length() - 1
        bits = bin(mask).count("1")
        return ((value & mask) >> shift) * 255 // ((1 << bits) - 1)

    for y in range(h):
        px = source_row(y)
        out = bytearray()
        for x in range(w):
            v = struct.unpack_from("<I", px, x * 4)[0]
            out += bytes((channel(v, masks[0]), channel(v, masks[1]),
                          channel(v, masks[2]),
                          channel(v, masks[3]) if use_alpha and masks[3] else 255))
        rows.append(bytes(out))
    return _png(w, h, rows)


# --------------------------------------------------------------------------
# saving
# --------------------------------------------------------------------------

def images_folder(project):
    media = project.GetSetting("projectMediaLocation")
    if not media:
        raise PasteError("This project has no media location set.\n\n"
                         "Set it in Project Settings > General Options > "
                         "Project Media Location.")
    if not os.path.isdir(media):
        raise PasteError(f"The project media location doesn't exist on this "
                         f"machine:\n\n{media}")

    # The project template keeps Images BESIDE the media folder, not inside it:
    #   <project>\<media location>\   and   <project>\Images\
    parent = os.path.dirname(os.path.normpath(media))
    if not parent or not os.path.isdir(parent):
        raise PasteError(f"Can't find the folder above the media location:\n\n{media}")

    # Reuse the template's folder whatever its capitalisation, so this never
    # creates a second "images" beside an existing "Images".
    existing = next((name for name in os.listdir(parent)
                     if name.lower() == FOLDER_NAME.lower()
                     and os.path.isdir(os.path.join(parent, name))), None)
    folder = os.path.join(parent, existing or FOLDER_NAME)
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as e:
        raise PasteError(f"Couldn't create the images folder:\n\n{folder}\n\n{e}")
    return folder


def unique_path(folder, filename):
    stem, ext = os.path.splitext(filename)
    candidate = os.path.join(folder, filename)
    n = 2
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f"{stem}_{n}{ext}")
        n += 1
    return candidate


def save_image(kind, payload, folder):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if kind == "file":
        source = os.path.abspath(payload)
        if os.path.normcase(os.path.dirname(source)) == os.path.normcase(folder):
            return source                       # already where it belongs
        target = unique_path(folder, os.path.basename(source))
        shutil.copy2(source, target)
        return target

    data = payload if kind == "png" else dib_to_png(payload)
    target = unique_path(folder, f"Clipboard_{stamp}.png")
    with open(target, "wb") as f:
        f.write(data)
    return target


# --------------------------------------------------------------------------
# Resolve
# --------------------------------------------------------------------------

def tc_to_frame(tc, fps):
    drop = ";" in tc
    h, m, s, f = map(int, tc.replace(";", ":").split(":"))
    rate = max(1, round(float(fps)))
    total = (h * 3600 + m * 60 + s) * rate + f
    if drop:
        d = 4 if rate == 60 else 2
        minutes = 60 * h + m
        total -= d * (minutes - minutes // 10)
    return total


def images_bin(media_pool):
    root = media_pool.GetRootFolder()
    for folder in root.GetSubFolderList() or []:
        if (folder.GetName() or "").lower() == BIN_NAME.lower():
            return folder
    return media_pool.AddSubFolder(root, BIN_NAME)


def still_length(clip, fps):
    """How many frames Resolve will give this still on the timeline."""
    try:
        frames = int(float(clip.GetClipProperty("Frames")))
        if frames > 1:
            return frames
    except (TypeError, ValueError):
        pass
    try:
        return max(1, tc_to_frame(clip.GetClipProperty("Duration"), fps))
    except Exception:
        return int(round(float(fps) * DEFAULT_STILL_SECONDS))


def free_track_above(timeline, start, end):
    """The track just above the highest one with anything in [start, end)."""
    highest = 0
    for index in range(1, (timeline.GetTrackCount("video") or 0) + 1):
        for item in timeline.GetItemListInTrack("video", index) or []:
            if item.GetStart() < end and item.GetEnd() > start:
                highest = index
                break
    return highest + 1


def place(project, timeline, clip):
    fps = timeline.GetSetting("timelineFrameRate") or 24
    playhead_tc = timeline.GetCurrentTimecode()
    record = tc_to_frame(playhead_tc, fps)
    length = still_length(clip, fps)

    track = free_track_above(timeline, record, record + length)
    while (timeline.GetTrackCount("video") or 0) < track:
        if not timeline.AddTrack("video"):
            raise PasteError("Couldn't add a video track for the image.")

    media_pool = project.GetMediaPool()
    placed = media_pool.AppendToTimeline([{
        "mediaPoolItem": clip,
        "mediaType": 1,                 # video only - a still has no audio
        "trackIndex": track,
        "recordFrame": record,
    }])

    # Leave the playhead where you had it.
    try:
        timeline.SetCurrentTimecode(playhead_tc)
    except Exception:
        pass

    if not placed:
        raise PasteError("The image was imported into the 'Images' bin but "
                         "couldn't be placed on this timeline.\n\nIf you're "
                         "inside a compound clip, that's a Resolve scripting "
                         "limit - drag it from the bin instead.")
    item = placed[0]
    if item.GetStart() != record:
        print(f"Note: asked for frame {record}, Resolve placed it at "
              f"{item.GetStart()}.")
    return track


def main():
    resolve = bmd.scriptapp("Resolve")
    project = resolve.GetProjectManager().GetCurrentProject()
    if not project:
        raise PasteError("No project is open.")
    timeline = project.GetCurrentTimeline()
    if not timeline:
        raise PasteError("Open a timeline first.")

    # Read the clipboard before touching anything else, so a non-image
    # clipboard costs nothing.
    kind, payload = clipboard_image()

    folder = images_folder(project)
    path = save_image(kind, payload, folder)

    media_pool = project.GetMediaPool()
    previous_bin = media_pool.GetCurrentFolder()
    target_bin = images_bin(media_pool)
    if target_bin:
        media_pool.SetCurrentFolder(target_bin)
    try:
        imported = media_pool.ImportMedia([path]) or []
    finally:
        # Put your bin selection back the way it was.
        if previous_bin:
            media_pool.SetCurrentFolder(previous_bin)

    if not imported:
        raise PasteError(f"Resolve couldn't import the image:\n\n{path}\n\n"
                         "The file is saved there - try dragging it in.")

    track = place(project, timeline, imported[0])
    print(f"Pasted {os.path.basename(path)} onto V{track} "
          f"({'copied file' if kind == 'file' else kind.upper()}) -> {path}")


try:
    main()
except PasteError as e:
    fail(str(e))
except Exception as e:
    import traceback
    traceback.print_exc()
    fail(f"Something unexpected went wrong:\n\n{e}")
