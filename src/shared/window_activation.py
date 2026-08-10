import ctypes
import ctypes.util
import base64
import os
import signal
import shutil
import subprocess
import weakref


_qt_raise_target_ref = None
_qt_raise_signal_number = signal.SIGWINCH
_qt_raise_signal_read_fd = None
_qt_raise_signal_write_fd = None
_qt_raise_signal_notifier = None


class _XClientMessageEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("message_type", ctypes.c_ulong),
        ("format", ctypes.c_int),
        ("data", ctypes.c_long * 5),
    ]


class _XEvent(ctypes.Union):
    _fields_ = [
        ("xclient", _XClientMessageEvent),
        ("pad", ctypes.c_long * 24),
    ]


def _load_x11():
    library_path = ctypes.util.find_library("X11")
    if not library_path:
        return None

    x11 = ctypes.CDLL(library_path)
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.restype = ctypes.c_int
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x11.XInternAtom.restype = ctypes.c_ulong
    x11.XQueryTree.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
        ctypes.POINTER(ctypes.c_uint),
    ]
    x11.XQueryTree.restype = ctypes.c_int
    x11.XGetWindowProperty.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_long,
        ctypes.c_long,
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
    ]
    x11.XGetWindowProperty.restype = ctypes.c_int
    x11.XMapRaised.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XMapRaised.restype = ctypes.c_int
    x11.XRaiseWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XRaiseWindow.restype = ctypes.c_int
    x11.XSetInputFocus.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    x11.XSetInputFocus.restype = ctypes.c_int
    x11.XSendEvent.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_long,
        ctypes.POINTER(_XEvent),
    ]
    x11.XSendEvent.restype = ctypes.c_int
    x11.XFlush.argtypes = [ctypes.c_void_p]
    x11.XFlush.restype = ctypes.c_int
    x11.XFree.argtypes = [ctypes.c_void_p]
    x11.XFree.restype = ctypes.c_int
    return x11


def _x11_children(x11, display, window):
    root = ctypes.c_ulong()
    parent = ctypes.c_ulong()
    children = ctypes.POINTER(ctypes.c_ulong)()
    child_count = ctypes.c_uint()
    ok = x11.XQueryTree(
        display,
        window,
        ctypes.byref(root),
        ctypes.byref(parent),
        ctypes.byref(children),
        ctypes.byref(child_count),
    )
    if not ok:
        return []
    try:
        return [children[index] for index in range(child_count.value)]
    finally:
        if children:
            x11.XFree(children)


def _x11_window_pid(x11, display, window, pid_atom):
    actual_type = ctypes.c_ulong()
    actual_format = ctypes.c_int()
    item_count = ctypes.c_ulong()
    bytes_after = ctypes.c_ulong()
    prop = ctypes.POINTER(ctypes.c_ubyte)()
    status = x11.XGetWindowProperty(
        display,
        window,
        pid_atom,
        0,
        1,
        False,
        0,
        ctypes.byref(actual_type),
        ctypes.byref(actual_format),
        ctypes.byref(item_count),
        ctypes.byref(bytes_after),
        ctypes.byref(prop),
    )
    if status != 0 or not prop:
        return None
    try:
        if actual_format.value != 32 or item_count.value < 1:
            return None
        return int(ctypes.cast(prop, ctypes.POINTER(ctypes.c_ulong))[0])
    finally:
        x11.XFree(prop)


def _find_x11_client_window_for_pid(x11, display, window, pid_atom, pid):
    stack = [window]
    while stack:
        current = stack.pop()
        if _x11_window_pid(x11, display, current, pid_atom) == pid:
            return current
        stack.extend(_x11_children(x11, display, current))
    return None


def _find_x11_windows_for_pid(x11, display, root, pid_atom, pid):
    for top_level_window in reversed(_x11_children(x11, display, root)):
        client_window = _find_x11_client_window_for_pid(x11, display, top_level_window, pid_atom, pid)
        if client_window is not None:
            return top_level_window, client_window
    return None, None


def _send_x11_client_message(x11, display, destination, window, message_type, data, event_mask=0):
    event = _XEvent()
    event.xclient.type = 33  # ClientMessage
    event.xclient.display = display
    event.xclient.window = window
    event.xclient.message_type = message_type
    event.xclient.format = 32
    for index, value in enumerate(data[:5]):
        event.xclient.data[index] = value
    return bool(x11.XSendEvent(display, destination, False, event_mask, ctypes.byref(event)))


def activate_window_for_pid(pid):
    if pid is None or pid <= 0 or not os.environ.get("DISPLAY"):
        return False

    x11 = _load_x11()
    if x11 is None:
        return False

    display = x11.XOpenDisplay(None)
    if not display:
        return False

    try:
        root = x11.XDefaultRootWindow(display)
        pid_atom = x11.XInternAtom(display, b"_NET_WM_PID", True)
        if not pid_atom:
            return False

        top_level_window, client_window = _find_x11_windows_for_pid(x11, display, root, pid_atom, int(pid))
        if not client_window:
            return False

        wm_change_state = x11.XInternAtom(display, b"WM_CHANGE_STATE", True)
        if wm_change_state:
            _send_x11_client_message(x11, display, client_window, client_window, wm_change_state, [1])

        for window in (top_level_window, client_window):
            if window:
                x11.XMapRaised(display, window)
                x11.XRaiseWindow(display, window)

        active_window = x11.XInternAtom(display, b"_NET_ACTIVE_WINDOW", True)
        if active_window:
            mask = (1 << 20) | (1 << 19)  # SubstructureRedirectMask | SubstructureNotifyMask
            _send_x11_client_message(x11, display, root, client_window, active_window, [2, 0, 0, 0, 0], mask)

        x11.XSetInputFocus(display, client_window, 2, 0)  # RevertToParent, CurrentTime

        x11.XFlush(display)
        return True
    finally:
        x11.XCloseDisplay(display)


def raise_qt_window(window):
    try:
        from PyQt5.QtCore import Qt
    except Exception:
        Qt = None

    if window is None:
        return

    if Qt is not None and hasattr(window, "windowState"):
        window.setWindowState((window.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
    if hasattr(window, "showNormal") and window.isMinimized():
        window.showNormal()
    elif hasattr(window, "show"):
        window.show()
    if hasattr(window, "raise_"):
        window.raise_()
    if hasattr(window, "activateWindow"):
        window.activateWindow()


def install_qt_window_raise_handler(window, signal_number=signal.SIGWINCH):
    global _qt_raise_signal_notifier
    global _qt_raise_signal_number
    global _qt_raise_signal_read_fd
    global _qt_raise_signal_write_fd
    global _qt_raise_target_ref
    _qt_raise_target_ref = weakref.ref(window)
    _qt_raise_signal_number = signal_number

    try:
        from PyQt5.QtCore import QSocketNotifier
    except Exception:
        QSocketNotifier = None

    def _handle_raise_request(signum, frame):
        return

    signal.signal(signal_number, _handle_raise_request)

    if QSocketNotifier is None:
        return

    if _qt_raise_signal_read_fd is None or _qt_raise_signal_write_fd is None:
        _qt_raise_signal_read_fd, _qt_raise_signal_write_fd = os.pipe()
        os.set_blocking(_qt_raise_signal_read_fd, False)
        os.set_blocking(_qt_raise_signal_write_fd, False)
        signal.set_wakeup_fd(_qt_raise_signal_write_fd)

    if _qt_raise_signal_notifier is not None:
        return

    _qt_raise_signal_notifier = QSocketNotifier(_qt_raise_signal_read_fd, QSocketNotifier.Read, window)

    def _drain_signal_pipe(_fd=None):
        should_raise = False
        while True:
            try:
                chunk = os.read(_qt_raise_signal_read_fd, 1024)
            except BlockingIOError:
                break
            if not chunk:
                break
            should_raise = should_raise or any(value == (int(_qt_raise_signal_number) & 0xFF) for value in chunk)

        if should_raise:
            target = _qt_raise_target_ref() if _qt_raise_target_ref is not None else None
            if target is not None:
                raise_qt_window(target)

    _qt_raise_signal_notifier.activated.connect(_drain_signal_pipe)


def request_qt_window_raise(pid, signal_number=signal.SIGWINCH):
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(int(pid), signal_number)
    except ProcessLookupError:
        return False
    except OSError:
        return False
    return True


def activate_windows_window_by_title(title_patterns):
    powershell = shutil.which("powershell.exe")
    if not powershell or not os.environ.get("WSL_INTEROP"):
        return False

    patterns = [str(pattern) for pattern in title_patterns if str(pattern).strip()]
    if not patterns:
        return False

    script = r'''
$patterns = @($args)
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;

public static class HalfLinacWindowActivator
{
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);

    public static IntPtr FindWindow(string[] patterns)
    {
        IntPtr found = IntPtr.Zero;
        EnumWindows(delegate(IntPtr hWnd, IntPtr lParam)
        {
            if (!IsWindowVisible(hWnd))
            {
                return true;
            }

            StringBuilder title = new StringBuilder(512);
            GetWindowText(hWnd, title, title.Capacity);
            string text = title.ToString();
            if (String.IsNullOrWhiteSpace(text))
            {
                return true;
            }

            foreach (string pattern in patterns)
            {
                if (!String.IsNullOrWhiteSpace(pattern) &&
                    text.IndexOf(pattern, StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    found = hWnd;
                    return false;
                }
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    public static bool Activate(string[] patterns)
    {
        IntPtr hWnd = FindWindow(patterns);
        if (hWnd == IntPtr.Zero)
        {
            return false;
        }

        ShowWindow(hWnd, 9);
        return SetForegroundWindow(hWnd);
    }
}
"@
if ([HalfLinacWindowActivator]::Activate($patterns)) { exit 0 }
exit 1
'''
    encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    try:
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded_script,
                *patterns,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0
