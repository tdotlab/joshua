"""Screenshots of a screen region, encoded as PNG with the standard library."""

import ctypes
import math
import struct
import zlib
from dataclasses import dataclass

from . import JoshuaError
from . import win32 as api


@dataclass(frozen=True)
class Frame:
    png: bytes
    width: int          # size of the image handed to the agent
    height: int
    source_width: int   # size of the area on screen
    source_height: int
    blank: bool         # every pixel black, which usually means capture failed


def scaled_size(width, height, max_width):
    if width <= max_width:
        return width, height
    return max_width, max(1, round(height * max_width / width))


def encode_png(width, height, bgrx):
    """PNG bytes from top-down 32-bit BGRX pixel data."""
    if len(bgrx) != width * height * 4:
        raise ValueError("pixel data does not match the image size")
    rgb = bytearray(width * height * 3)
    rgb[0::3] = bgrx[2::4]
    rgb[1::3] = bgrx[1::4]
    rgb[2::3] = bgrx[0::4]
    stride = width * 3
    raw = b"".join(b"\0" + rgb[row * stride:(row + 1) * stride] for row in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 4)) + chunk(b"IEND", b""))


def corner_radius(dpi, scale):
    """How far in to mask, in screenshot pixels, for a window on a display of this DPI.

    Windows 11 rounds window corners with an 8 pixel radius at 96 DPI.
    """
    radius = math.ceil(8 * dpi / 96 * scale)
    # Downscaling blends neighbouring pixels, so take one more.
    return radius + (1 if scale < 1 else 0)


def blacken_corners(pixels, width, height, radius):
    """Black out, in place, what lies outside the rounded corners of a 32-bit picture.

    Those pixels belong to whatever is behind the game, which the agent must not see.
    """
    radius = min(radius, width // 2, height // 2)
    for row in range(radius):
        for column in range(radius):
            # A pixel short of the radius, to take in the half-covered pixels along the curve.
            if math.hypot(radius - column - 0.5, radius - row - 0.5) <= radius - 1:
                continue
            for x in (column, width - 1 - column):
                for y in (row, height - 1 - row):
                    at = (y * width + x) * 4
                    pixels[at:at + 4] = bytes(4)


def grab(left, top, width, height, max_width=1280, dpi=96):
    """Capture a window's area, downscaled to at most max_width pixels across.

    dpi is that of the display showing it, which sets how round its corners are.
    """
    if width <= 0 or height <= 0:
        raise JoshuaError("the game window has no visible area")
    out_width, out_height = scaled_size(width, height, max_width)
    screen = api.GetDC(None)
    memory = api.CreateCompatibleDC(screen)
    bitmap = previous = None
    try:
        info = api.BITMAPINFOHEADER(ctypes.sizeof(api.BITMAPINFOHEADER), out_width, -out_height,
                                    1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        bitmap = api.CreateDIBSection(memory, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not bitmap:
            raise JoshuaError("could not allocate the screenshot bitmap")
        previous = api.SelectObject(memory, bitmap)
        api.SetStretchBltMode(memory, api.HALFTONE)
        api.SetBrushOrgEx(memory, 0, 0, None)
        if not api.StretchBlt(memory, 0, 0, out_width, out_height, screen, left, top, width, height,
                              api.SRCCOPY | api.CAPTUREBLT):
            raise JoshuaError("the screen could not be captured (locked or disconnected desktop?)")
        api.GdiFlush()
        pixels = bytearray(ctypes.string_at(bits, out_width * out_height * 4))
    finally:
        if previous:
            api.SelectObject(memory, previous)
        if bitmap:
            api.DeleteObject(bitmap)
        api.DeleteDC(memory)
        api.ReleaseDC(None, screen)
    blacken_corners(pixels, out_width, out_height, corner_radius(dpi, out_width / width))
    blank = not any(pixels[0::4]) and not any(pixels[1::4]) and not any(pixels[2::4])
    return Frame(encode_png(out_width, out_height, pixels), out_width, out_height, width, height, blank)
