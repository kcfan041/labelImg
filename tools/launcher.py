#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Start labelImg without a terminal: used by "Start labelImg.app" (macOS) and
"Start labelImg.bat" (Windows).

1. Uses <repo>/.venv; creates it and installs requirements.txt on the first start, or when
   the packages are missing / broken.
2. Runs labelImg.py with the venv's Python and no console window.
3. Everything (pip output, Python errors) goes to <repo>/launcher.log. When something fails a
   dialog shows the end of it, since there is no terminal to read.

Only the standard library is used. The .app / .bat only start it with Python 3.8 or newer.
"""
import os
import subprocess
import sys
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = os.path.join(REPO, '.venv')
REQUIREMENTS = os.path.join(REPO, 'requirements.txt')
LOCK = os.path.join(REPO, '.venv.installing')
MIN_PYTHON = (3, 8)
TITLE = 'labelImg'
WINDOWS = sys.platform == 'win32'
MAC = sys.platform == 'darwin'
# Windows: start console programs (python.exe, pip) without popping up a console window
NO_WINDOW = 0x08000000 if WINDOWS else 0
# Set by the tests: write dialog texts to the log instead of showing them
NO_DIALOG = bool(os.environ.get('LABELIMG_LAUNCHER_NO_DIALOG'))


def log_path():
    path = os.path.join(REPO, 'launcher.log')
    try:
        with open(path, 'a'):
            pass
        return path
    except OSError:  # read-only install folder
        import tempfile
        return os.path.join(tempfile.gettempdir(), 'labelImg-launcher.log')


LOG = log_path()


def trim_log(keep=3000):
    try:
        with open(LOG, encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        if len(lines) > keep:
            with open(LOG, 'w', encoding='utf-8') as f:
                f.writelines(lines[-keep:])
    except OSError:
        pass


def log(text):
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write('[%s] %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), text))


def log_tail(n=15):
    try:
        with open(LOG, encoding='utf-8', errors='replace') as f:
            return ''.join(f.readlines()[-n:]).strip()
    except OSError:
        return ''


# ---- dialogs -------------------------------------------------------------------------------

def _mac_dialog(text, wait=True, seconds=None):
    script = ['on run argv',
              'display dialog (item 1 of argv) with title "%s" buttons {"OK"} default button 1%s'
              % (TITLE, ' giving up after %d' % seconds if seconds else ''),
              'end run']
    args = ['osascript']
    for line in script:
        args += ['-e', line]
    args.append(text)
    if wait:
        subprocess.call(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return None
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _win_box(text, title):
    import ctypes
    MB_ICONINFORMATION, MB_SETFOREGROUND = 0x40, 0x10000
    ctypes.windll.user32.MessageBoxW(None, text, title, MB_ICONINFORMATION | MB_SETFOREGROUND)


def message(text):
    """Blocking message box."""
    log('dialog: ' + text.replace('\n', ' | '))
    if NO_DIALOG:
        return
    if MAC:
        _mac_dialog(text)
    elif WINDOWS:
        _win_box(text, TITLE)
    else:
        sys.stderr.write(text + '\n')


class Notice(object):
    """Non-blocking 'please wait' box, closed again with close()."""

    def __init__(self, text):
        log('notice: ' + text.replace('\n', ' | '))
        self.proc = None
        self.title = TITLE + ' '  # distinct title so close() finds this box only
        if NO_DIALOG:
            return
        if MAC:
            self.proc = _mac_dialog(text, wait=False, seconds=900)
        elif WINDOWS:
            threading.Thread(target=_win_box, args=(text, self.title), daemon=True).start()

    def close(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
        if WINDOWS and not NO_DIALOG:
            import ctypes
            hwnd = ctypes.windll.user32.FindWindowW(None, self.title)
            if hwnd:
                ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE


def fail(text):
    tail = log_tail()
    message('%s\n\n記錄檔（log）：\n%s%s' % (text, LOG, '\n\n最後的訊息：\n' + tail[-1500:] if tail else ''))
    sys.exit(1)


# ---- venv ----------------------------------------------------------------------------------

def venv_python(gui=False):
    if WINDOWS:
        return os.path.join(VENV, 'Scripts', 'pythonw.exe' if gui else 'python.exe')
    return os.path.join(VENV, 'bin', 'python3')


def run_logged(args):
    """Run a command with its output appended to the log; returns the exit code."""
    log('$ ' + ' '.join('"%s"' % a if ' ' in a else a for a in args))
    with open(LOG, 'a', encoding='utf-8') as out:
        return subprocess.call(args, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                               cwd=REPO, creationflags=NO_WINDOW)


def venv_ready():
    py = venv_python()
    if not os.path.isfile(py):
        return False
    return subprocess.call([py, '-c', 'import PyQt5.QtWidgets, lxml'], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, cwd=REPO, creationflags=NO_WINDOW) == 0


def base_python():
    """A Python to create the venv with: the one running this script, unless it is the venv's."""
    exe = sys.executable
    if WINDOWS and exe.lower().endswith('pythonw.exe'):
        console = exe[:-len('pythonw.exe')] + 'python.exe'
        if os.path.isfile(console):
            exe = console
    return exe


def install():
    """Create / repair .venv. Another launcher already installing -> tell the user and stop."""
    if os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) < 20 * 60:
        message('labelImg 正在進行第一次安裝，請稍候，完成後會自動開啟。')
        sys.exit(0)
    with open(LOCK, 'w') as f:
        f.write(str(os.getpid()))
    notice = Notice('第一次啟動，正在安裝需要的套件（PyQt5、lxml）。\n\n'
                    '需要網路，可能要幾分鐘，完成後 labelImg 會自動開啟。')
    try:
        repair = os.path.isfile(venv_python())
        if not repair:
            log('creating %s with %s (Python %s)' % (VENV, base_python(), sys.version.split()[0]))
            if run_logged([base_python(), '-m', 'venv', '--clear', VENV]) != 0:
                fail('無法建立 Python 虛擬環境（.venv）。')
        py = venv_python()
        if run_logged([py, '-m', 'pip', 'install', '--disable-pip-version-check',
                       '--upgrade', 'pip']) != 0:
            log('pip upgrade failed; trying the install anyway')
        # A damaged package still looks installed to pip, so a repair reinstalls everything
        args = [py, '-m', 'pip', 'install', '--disable-pip-version-check', '-r', REQUIREMENTS]
        if repair:
            log('packages missing or broken: reinstalling')
            args.insert(4, '--force-reinstall')
        if run_logged(args) != 0:
            fail('安裝套件失敗。請確認網路連線後再試一次。')
        if not venv_ready():
            fail('套件安裝完成，但 PyQt5 仍無法載入。')
        log('install done')
    finally:
        notice.close()
        try:
            os.remove(LOCK)
        except OSError:
            pass


# ---- main ----------------------------------------------------------------------------------

def main():
    trim_log()
    log('---- start (launcher Python %s at %s)' % (sys.version.split()[0], sys.executable))
    if sys.version_info < MIN_PYTHON:
        fail('需要 Python %d.%d 以上，目前是 %s。\n請到 https://www.python.org/downloads/ 安裝新版。'
             % (MIN_PYTHON + (sys.version.split()[0],)))
    if not venv_ready():
        install()

    env = dict(os.environ)
    env['PYTHONUNBUFFERED'] = '1'  # so the last error before a crash still reaches the log
    args = [venv_python(gui=True), os.path.join(REPO, 'labelImg.py')]
    log('$ ' + ' '.join(args))
    with open(LOG, 'a', encoding='utf-8') as out:
        proc = subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                cwd=REPO, env=env, creationflags=NO_WINDOW)
    code = proc.wait()
    log('labelImg exited with code %s' % code)
    # 0: normal quit. Negative: stopped by a signal (killed / logged out) except crash signals.
    if code != 0 and not (code < 0 and -code not in (6, 11)):
        fail('labelImg 意外結束（代碼 %s）。' % code)


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback
        log(traceback.format_exc())
        fail('啟動器發生錯誤。')
