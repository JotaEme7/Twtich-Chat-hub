"""
Twitch Chat Hub
Ícone na bandeja do sistema (ao lado do relógio) que abre APENAS os chats
dos canais da Twitch que você cadastrar. Suporta várias contas ao mesmo
tempo: cada conta usa um perfil separado do navegador, com o próprio login.

Dependências: pip install pystray pillow
Execute com duplo clique (.pyw não abre janela de console).
"""

import copy
import html
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

import pystray
from PIL import Image, ImageDraw

APP_NAME = "TwitchChatHub"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_FILE = os.path.join(CONFIG_DIR, "channels.json")
PROFILES_DIR = os.path.join(CONFIG_DIR, "profiles")
CHAT_URL = "https://www.twitch.tv/popout/{channel}/chat?popout="
CHANNEL_RE = re.compile(r"^[a-zA-Z0-9_]{3,25}$")
ID_RE = re.compile(r"^[a-z0-9_]{1,20}$")

# Conta 1 usa o perfil normal do navegador (o login que você já tem).
# As outras usam perfis próprios, onde você entra uma vez e fica salvo.
DEFAULT_ACCOUNTS = [
    {"id": "acc1", "name": "Conta 1", "channels": None, "default_profile": True},
    {"id": "acc2", "name": "Conta 2", "channels": None, "default_profile": False},
    {"id": "acc3", "name": "Conta 3", "channels": None, "default_profile": False},
    {"id": "acc4", "name": "Conta 4", "channels": None, "default_profile": False},
]

_lock = threading.Lock()


# ---------------------------------------------------------------- config
def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}

    raw_channels = data.get("channels", [])
    channels = [c for c in raw_channels if isinstance(c, str) and CHANNEL_RE.match(c)] \
        if isinstance(raw_channels, list) else []

    accounts, seen = [], set()
    raw_accounts = data.get("accounts")
    if isinstance(raw_accounts, list):
        for a in raw_accounts:
            if not isinstance(a, dict):
                continue
            aid = a.get("id")
            if not isinstance(aid, str) or not ID_RE.match(aid) or aid in seen:
                continue
            seen.add(aid)
            name = str(a.get("name") or aid).strip()[:30] or aid
            ch = a.get("channels")
            if isinstance(ch, list):
                ch = [c for c in ch if isinstance(c, str) and CHANNEL_RE.match(c)]
            else:
                ch = None
            accounts.append({
                "id": aid,
                "name": name,
                "channels": ch,
                "default_profile": bool(a.get("default_profile", False)),
            })
    if not accounts:
        accounts = copy.deepcopy(DEFAULT_ACCOUNTS)

    browser = data.get("browser")
    if not isinstance(browser, str) or not browser:
        browser = None
    return {"channels": channels, "accounts": accounts, "browser": browser}


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_FILE)


def update_config(fn):
    with _lock:
        cfg = load_config()
        fn(cfg)
        save_config(cfg)


def get_account(cfg, acc_id):
    for a in cfg["accounts"]:
        if a["id"] == acc_id:
            return a
    return None


def account_channels(cfg, acc):
    """Canais que essa conta mostra (None = todos)."""
    if acc["channels"] is None:
        return list(cfg["channels"])
    return [c for c in cfg["channels"] if c in acc["channels"]]


def normalize_channel(text):
    """Aceita 'canal', '@canal' ou URL da twitch."""
    text = (text or "").strip().lower()
    text = re.sub(r"^https?://(www\.)?twitch\.tv/(popout/)?", "", text)
    text = text.split("/")[0].split("?")[0].lstrip("@")
    return text if CHANNEL_RE.match(text) else None


# ---------------------------------------------------------------- navegador
def find_browser(cfg):
    """Navegador escolhido por você, ou Chrome / Edge / Brave."""
    custom = cfg.get("browser")
    if custom and os.path.isfile(custom):
        return custom

    local = os.environ.get("LOCALAPPDATA", "")
    pf = os.environ.get("PROGRAMFILES", "")
    pf86 = os.environ.get("PROGRAMFILES(X86)", "")
    candidates = []
    for base in (pf, pf86, local):
        if not base:
            continue
        candidates += [
            os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(base, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
        ]
    for exe in ("chrome", "msedge", "brave"):
        p = shutil.which(exe)
        if p:
            candidates.append(p)
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


# ---------------------------------------------------------------- rastrear/fechar janelas (Windows)
# O hub guarda quais janelas do navegador ele abriu, comparando as janelas
# existentes antes e depois de cada abertura, para poder fechá-las depois.
_tracked = set()
_tracked_lock = threading.Lock()
WM_CLOSE = 0x0010


def _user32():
    """user32.dll com os tipos de argumento certos (handles de 64 bits)."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.IsWindow.argtypes = [wintypes.HWND]
    u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    return u


def _list_windows():
    """Handles das janelas visíveis de navegadores baseados em Chromium."""
    if os.name != "nt":
        return set()
    try:
        import ctypes
        from ctypes import wintypes
        u = _user32()
        found = set()

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _callback(hwnd, lparam):
            if u.IsWindowVisible(hwnd):
                cls = ctypes.create_unicode_buffer(256)
                u.GetClassNameW(hwnd, cls, 256)
                if cls.value.startswith("Chrome_WidgetWin") and u.GetWindowTextLengthW(hwnd) > 0:
                    found.add(int(hwnd))
            return True

        u.EnumWindows(_callback, 0)
        return found
    except Exception:
        return set()


def _track_new_windows(before):
    """Depois de abrir uma janela, anota os handles novos que aparecerem."""
    def worker():
        deadline = time.time() + 12
        first_seen = None
        while time.time() < deadline:
            new = _list_windows() - before
            if new:
                with _tracked_lock:
                    _tracked.update(new)
                if first_seen is None:
                    first_seen = time.time()
            if first_seen is not None and time.time() - first_seen > 1.5:
                break
            time.sleep(0.4)

    threading.Thread(target=worker, daemon=True).start()


def close_tracked_windows():
    """Pede ao Windows para fechar as janelas que o hub abriu."""
    if os.name != "nt":
        return
    with _tracked_lock:
        handles = list(_tracked)
        _tracked.clear()
    if not handles:
        return
    try:
        u = _user32()
        for h in handles:
            if u.IsWindow(h):
                u.PostMessageW(h, WM_CLOSE, 0, 0)
    except Exception:
        pass


def launch_window(url, acc, size, pos):
    """Abre uma janela limpa (sem abas) já no perfil da conta."""
    cfg = load_config()
    browser = find_browser(cfg)
    if browser:
        args = [browser]
        if acc and not acc.get("default_profile"):
            pdir = os.path.join(PROFILES_DIR, acc["id"])
            os.makedirs(pdir, exist_ok=True)
            args.append(f"--user-data-dir={pdir}")
        args += [
            "--no-first-run",
            "--no-default-browser-check",
            f"--app={url}",
            f"--window-size={size}",
            f"--window-position={pos[0]},{pos[1]}",
        ]
        before = _list_windows()
        try:
            subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            _track_new_windows(before)
            return
        except OSError:
            pass
    webbrowser.open(url)


# ---------------------------------------------------------------- página com todos os chats
# A Twitch só aceita embed em páginas com um domínio válido (parent), então
# servimos a página em http://localhost em vez de abrir um arquivo local.
PREFERRED_PORT = 8765
_server = None
_server_port = None

PAGE_TEMPLATE = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<title>{title}</title>
<style>
  html, body {{ margin:0; height:100%; background:#0e0e10; color:#efeff1;
               font-family:Segoe UI, Arial, sans-serif; }}
  #grid {{ display:flex; height:100%; }}
  .col {{ flex:1 1 0; min-width:220px; display:flex; flex-direction:column;
         border-right:1px solid #26262c; }}
  .col:last-child {{ border-right:none; }}
  .head {{ display:flex; justify-content:space-between; align-items:center;
          padding:4px 10px; background:#18181b; font-size:13px; font-weight:600; }}
  .head button {{ background:none; border:none; color:#adadb8; cursor:pointer; font-size:16px; }}
  .head button:hover {{ color:#fff; }}
  iframe {{ flex:1; border:0; width:100%; }}
  .empty {{ margin:auto; color:#adadb8; }}
</style></head>
<body><div id="grid">{columns}</div>
<script>
  document.querySelectorAll('.head button').forEach(function (b) {{
    b.onclick = function () {{ b.closest('.col').remove(); }};
  }});
</script></body></html>"""


def build_page(acc_id=None):
    cfg = load_config()
    acc = get_account(cfg, acc_id) if acc_id else None
    channels = account_channels(cfg, acc) if acc else cfg["channels"]
    title = "Twitch Chat Hub" + (f" - {acc['name']}" if acc else "")
    if not channels:
        columns = '<div class="empty">Nenhum canal para mostrar.</div>'
    else:
        cols = []
        for c in channels:
            c = html.escape(c)
            cols.append(
                f'<div class="col"><div class="head"><span>{c}</span>'
                f'<button title="Fechar esta coluna">&times;</button></div>'
                f'<iframe src="https://www.twitch.tv/embed/{c}/chat'
                f'?parent=localhost&darkpopout"></iframe></div>'
            )
        columns = "".join(cols)
    return PAGE_TEMPLATE.format(title=html.escape(title), columns=columns)


class _PageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path not in ("", "/"):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        acc_id = (parse_qs(parsed.query).get("a") or [None])[0]
        body = build_page(acc_id).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # silencioso


def ensure_server():
    global _server, _server_port
    if _server is not None:
        return _server_port
    for port in (PREFERRED_PORT, 0):
        try:
            _server = ThreadingHTTPServer(("127.0.0.1", port), _PageHandler)
            break
        except OSError:
            continue
    if _server is None:
        return None
    _server_port = _server.server_address[1]
    threading.Thread(target=_server.serve_forever, daemon=True).start()
    return _server_port


# ---------------------------------------------------------------- abrir chats
def open_account_window(acc_id, index=0):
    cfg = load_config()
    acc = get_account(cfg, acc_id)
    if not acc or not account_channels(cfg, acc):
        return
    port = ensure_server()
    if port is None:
        return
    url = f"http://localhost:{port}/?a={acc_id}"
    launch_window(url, acc, "1400,800", (40 + 40 * index, 40 + 40 * index))


def open_account_separate(acc_id):
    cfg = load_config()
    acc = get_account(cfg, acc_id)
    if not acc:
        return
    for i, c in enumerate(account_channels(cfg, acc)):
        launch_window(CHAT_URL.format(channel=c), acc, "400,700", (40 + 30 * i, 40 + 30 * i))


def open_all_accounts(icon, item):
    cfg = load_config()
    for i, acc in enumerate(cfg["accounts"]):
        open_account_window(acc["id"], i)


# ---------------------------------------------------------------- iniciar com o Windows
def _run_key():
    import winreg
    return winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_ALL_ACCESS,
    )


def _launch_command():
    if getattr(sys, "frozen", False):  # rodando como .exe
        return f'"{sys.executable}"'
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        exe = exe[:-10] + "pythonw.exe"
    return f'"{exe}" "{os.path.abspath(__file__)}"'


def autostart_enabled():
    if os.name != "nt":
        return False
    try:
        import winreg
        with _run_key() as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except OSError:
        return False


def toggle_autostart(icon, item):
    if os.name != "nt":
        return
    import winreg
    with _run_key() as key:
        if autostart_enabled():
            winreg.DeleteValue(key, APP_NAME)
        else:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _launch_command())


# ---------------------------------------------------------------- diálogos (tkinter em thread própria)
def _with_tk(fn):
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return fn(root)
    finally:
        root.destroy()


def add_channel_dialog(icon, item):
    def worker():
        raw = _with_tk(lambda r: simpledialog.askstring(
            "Adicionar canal", "Nome do canal (ou link da Twitch):", parent=r))
        if raw is None:
            return
        channel = normalize_channel(raw)
        if not channel:
            _with_tk(lambda r: messagebox.showerror(
                "Canal inválido", "Use 3 a 25 caracteres: letras, números ou _.", parent=r))
            return

        def mod(cfg):
            if channel not in cfg["channels"]:
                cfg["channels"].append(channel)

        update_config(mod)
        icon.update_menu()

    threading.Thread(target=worker, daemon=True).start()


def make_rename(acc_id):
    def _action(icon, item):
        def worker():
            acc = get_account(load_config(), acc_id)
            if not acc:
                return
            new = _with_tk(lambda r: simpledialog.askstring(
                "Renomear conta", "Novo nome:", initialvalue=acc["name"], parent=r))
            new = (new or "").strip()[:30]
            if not new:
                return

            def mod(cfg):
                a = get_account(cfg, acc_id)
                if a:
                    a["name"] = new

            update_config(mod)
            icon.update_menu()

        threading.Thread(target=worker, daemon=True).start()
    return _action


def choose_browser(icon, item):
    def worker():
        path = _with_tk(lambda r: filedialog.askopenfilename(
            parent=r, title="Escolha o navegador (.exe)",
            filetypes=[("Programa", "*.exe")]))
        if not path:
            return
        update_config(lambda cfg: cfg.__setitem__("browser", path))
        icon.update_menu()

    threading.Thread(target=worker, daemon=True).start()


def auto_browser(icon, item):
    update_config(lambda cfg: cfg.__setitem__("browser", None))
    icon.update_menu()


# ---------------------------------------------------------------- ações do menu
def make_open_window(acc_id):
    def _action(icon, item):
        cfg = load_config()
        ids = [a["id"] for a in cfg["accounts"]]
        open_account_window(acc_id, ids.index(acc_id) if acc_id in ids else 0)
    return _action


def make_open_separate(acc_id):
    def _action(icon, item):
        open_account_separate(acc_id)
    return _action


def make_toggle_channel(acc_id, channel):
    def _action(icon, item):
        def mod(cfg):
            acc = get_account(cfg, acc_id)
            if acc is None:
                return
            cur = list(cfg["channels"]) if acc["channels"] is None else list(acc["channels"])
            if channel in cur:
                cur.remove(channel)
            else:
                cur.append(channel)
            acc["channels"] = cur

        update_config(mod)
        icon.update_menu()
    return _action


def make_channel_checked(acc_id, channel):
    def _checked(item):
        acc = get_account(load_config(), acc_id)
        return bool(acc) and (acc["channels"] is None or channel in acc["channels"])
    return _checked


def make_all_channels(acc_id):
    def _action(icon, item):
        def mod(cfg):
            acc = get_account(cfg, acc_id)
            if acc is not None:
                acc["channels"] = None

        update_config(mod)
        icon.update_menu()
    return _action


def make_all_checked(acc_id):
    def _checked(item):
        acc = get_account(load_config(), acc_id)
        return bool(acc) and acc["channels"] is None
    return _checked


def make_toggle_profile(acc_id):
    def _action(icon, item):
        def mod(cfg):
            acc = get_account(cfg, acc_id)
            if acc is not None:
                acc["default_profile"] = not acc["default_profile"]

        update_config(mod)
        icon.update_menu()
    return _action


def make_profile_checked(acc_id):
    def _checked(item):
        acc = get_account(load_config(), acc_id)
        return bool(acc) and acc["default_profile"]
    return _checked


def make_remove(channel):
    def _action(icon, item):
        def mod(cfg):
            cfg["channels"] = [c for c in cfg["channels"] if c != channel]
            for a in cfg["accounts"]:
                if a["channels"] is not None:
                    a["channels"] = [c for c in a["channels"] if c != channel]

        update_config(mod)
        icon.update_menu()
    return _action


def close_windows(icon, item):
    close_tracked_windows()


def quit_app(icon, item):
    close_tracked_windows()
    time.sleep(0.3)
    icon.stop()


# ---------------------------------------------------------------- menu
def account_submenu(cfg, acc):
    aid = acc["id"]
    items = [
        pystray.MenuItem("Abrir chats numa janela só", make_open_window(aid)),
        pystray.MenuItem("Abrir chats em janelas separadas", make_open_separate(aid)),
        pystray.Menu.SEPARATOR,
    ]
    if cfg["channels"]:
        ch_items = [pystray.MenuItem(
            "Todos os canais", make_all_channels(aid), checked=make_all_checked(aid))]
        for c in cfg["channels"]:
            ch_items.append(pystray.MenuItem(
                c, make_toggle_channel(aid, c), checked=make_channel_checked(aid, c)))
        items.append(pystray.MenuItem("Canais desta conta", pystray.Menu(*ch_items)))
    items.append(pystray.MenuItem(
        "Usar perfil normal do navegador",
        make_toggle_profile(aid), checked=make_profile_checked(aid)))
    items.append(pystray.MenuItem("Renomear conta...", make_rename(aid)))
    return items


def build_menu_items():
    cfg = load_config()
    if cfg["channels"]:
        yield pystray.MenuItem("Abrir todas as contas", open_all_accounts, default=True)
        yield pystray.MenuItem("Fechar todas as janelas", close_windows)
    else:
        yield pystray.MenuItem("(adicione um canal para começar)", None, enabled=False)
    yield pystray.Menu.SEPARATOR
    for acc in cfg["accounts"]:
        yield pystray.MenuItem(acc["name"], pystray.Menu(*account_submenu(cfg, acc)))
    yield pystray.Menu.SEPARATOR
    yield pystray.MenuItem("Adicionar canal...", add_channel_dialog)
    if cfg["channels"]:
        yield pystray.MenuItem(
            "Remover canal",
            pystray.Menu(*[pystray.MenuItem(c, make_remove(c)) for c in cfg["channels"]]),
        )
    yield pystray.Menu.SEPARATOR
    yield pystray.MenuItem("Escolher navegador...", choose_browser)
    if cfg.get("browser"):
        yield pystray.MenuItem("Voltar ao navegador automático", auto_browser)
    yield pystray.MenuItem(
        "Iniciar com o Windows", toggle_autostart,
        checked=lambda item: autostart_enabled(),
    )
    yield pystray.MenuItem("Sair", quit_app)


# ---------------------------------------------------------------- ícone
def make_icon_image():
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 6, 60, 46), radius=10, fill=(145, 70, 255, 255))
    d.polygon([(16, 44), (16, 60), (32, 44)], fill=(145, 70, 255, 255))
    for x in (20, 32, 44):
        d.ellipse((x - 4, 22, x + 4, 30), fill=(255, 255, 255, 255))
    return img


def main():
    icon = pystray.Icon(
        APP_NAME,
        make_icon_image(),
        "Twitch Chat Hub",
        menu=pystray.Menu(build_menu_items),
    )
    icon.run()


if __name__ == "__main__":
    main()
