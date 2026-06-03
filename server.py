#!/usr/bin/env python3
"""
Панель администратора системы оповещения.
Запуск:  python3 server.py
Открыть: http://localhost:8080
Зависимости: только стандартная библиотека Python 3.
"""
import json
import secrets
import subprocess
import sys
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ─────────────── Настройки ───────────────
PANEL_HOST     = "0.0.0.0"
PANEL_PORT     = 8080
ADMIN_LOGIN    = "admin"
ADMIN_PASSWORD = "admin123"             # ← поменяй
AGENT_TOKEN    = "supersecrettoken123"  # должен совпадать с agent.py
SESSION_TTL    = 3600

# ─────────────── Настройки рассылки ───────────────
# Меняй значения здесь для постоянного эффекта.
# Через панель (вкладка Настройки) — изменения до перезапуска сервера.
#
# max_workers  — сколько хостов опрашивать параллельно
#   до 50 хостов  → 10-20,  до 200 → 30-50,  400+ → 50-100
# send_timeout — секунд ждать ответа от агента на каждом хосте

SEND_SETTINGS = {
    "max_workers":  50,   # ← меняй
    "send_timeout": 3,    # ← меняй (секунды)
}

# API ключ для Ansible и внешних скриптов
# Используется в endpoint /api/ansible (без браузерной авторизации)
# Для постоянного изменения меняй ANSIBLE_SETTINGS["api_key"]
ANSIBLE_SETTINGS = {"api_key": "ansible-secret-key"}  # ← поменяй

BASE_DIR       = Path(__file__).parent
DATA_DIR       = BASE_DIR / "data"
INDEX_FILE     = BASE_DIR / "index.html"
SENDER_SCRIPT  = BASE_DIR / "sender.py"
HOSTS_FILE     = DATA_DIR / "hosts.json"
TEMPLATES_FILE = DATA_DIR / "templates.json"
HISTORY_FILE   = DATA_DIR / "history.json"

_sessions: dict = {}


# ─────────────── JSON helpers ───────────────
def jread(path: Path) -> list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

def jwrite(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ─────────────── Первичные данные ───────────────
def init_db():
    DATA_DIR.mkdir(exist_ok=True)
    if not HOSTS_FILE.exists():
        jwrite(HOSTS_FILE, [
            {"id": 1, "name": "localhost", "address": "127.0.0.1",
             "group": "Тест", "enabled": True},
        ])
    if not TEMPLATES_FILE.exists():
        jwrite(TEMPLATES_FILE, [
            {"id": 1, "name": "Воздушная тревога",
             "title": "🚨 ВОЗДУШНАЯ ТРЕВОГА",
             "body": "## Внимание!\n\nОбъявлена **воздушная тревога**.\n\n> Немедленно следуйте в укрытие.",
             "level": "critical"},
            {"id": 2, "name": "Отбой тревоги",
             "title": "✅ Отбой тревоги",
             "body": "## Угроза миновала\n\nМожно вернуться на рабочие места.",
             "level": "info"},
            {"id": 3, "name": "Перезагрузка",
             "title": "⚠️ Плановая перезагрузка",
             "body": "## Внимание!\n\nСервер будет перезагружен **в 18:00**.\n\nСохраните документы.",
             "level": "warning"},
        ])
    if not HISTORY_FILE.exists():
        jwrite(HISTORY_FILE, [])


# ─────────────── Сессии ───────────────
def session_create() -> str:
    tok = secrets.token_hex(32)
    _sessions[tok] = datetime.now().timestamp()
    return tok

def session_valid(tok: str) -> bool:
    if not tok or tok not in _sessions:
        return False
    if datetime.now().timestamp() - _sessions[tok] > SESSION_TTL:
        del _sessions[tok]
        return False
    return True

def get_session(headers) -> str:
    for part in headers.get("Cookie", "").split(";"):
        p = part.strip()
        if p.startswith("session="):
            return p[8:]
    return ""


# ─────────────── Отправка ───────────────
def _send_one(title: str, body: str, level: str, host: dict,
              msg_type: str = "", timeout: int = 0) -> dict:
    """Отправить одно уведомление на один хост. Вызывается в потоке."""
    addr = host.get("address", "")
    name = host.get("name", addr)
    url  = f"http://{addr}:9988"
    payload = {"title": title, "message": body, "level": level}
    if msg_type:
        payload["type"]    = msg_type
        payload["timeout"] = timeout
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Content-Type":   "application/json",
            "X-Token":        AGENT_TOKEN,
            "Content-Length": str(len(data)),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=SEND_SETTINGS["send_timeout"]) as resp:
            ok = (resp.status == 200)
    except Exception:
        ok = False
    return {"host": name, "address": addr, "ok": ok}


def do_notify(title: str, body: str, level: str, hosts: list,
             msg_type: str = "", timeout: int = 0) -> list:
    """
    Параллельная рассылка уведомлений.
    MAX_WORKERS и SEND_TIMEOUT задаются в настройках сверху файла.
    """
    workers = min(SEND_SETTINGS["max_workers"], len(hosts))
    results = [None] * len(hosts)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_idx = {
            pool.submit(_send_one, title, body, level, h, msg_type, timeout): i
            for i, h in enumerate(hosts)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                h = hosts[idx]
                results[idx] = {
                    "host":    h.get("name", h.get("address", "")),
                    "address": h.get("address", ""),
                    "ok":      False,
                }

    hist = jread(HISTORY_FILE)
    hist.insert(0, {
        "id":      int(datetime.now().timestamp() * 1000),
        "time":    datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        "title":   title,
        "level":   level,
        "total":   len(results),
        "ok":      sum(1 for r in results if r["ok"]),
        "results": results,
    })
    jwrite(HISTORY_FILE, hist[:200])
    return results


def do_notify_ansible(title: str, body: str, level: str,
                      msg_type: str, timeout: int, hosts: list) -> list:
    """
    Отправка с поддержкой type и timeout — для Ansible endpoint.
    """
    def send_one(host):
        addr = host.get("address", "")
        name = host.get("name", addr)
        url  = f"http://{addr}:9988"
        data = json.dumps({
            "title":   title,
            "message": body,
            "level":   level,
            "type":    msg_type,
            "timeout": timeout,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={
                "Content-Type":   "application/json",
                "X-Token":        AGENT_TOKEN,
                "Content-Length": str(len(data)),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                req, timeout=SEND_SETTINGS["send_timeout"]
            ) as resp:
                ok = (resp.status == 200)
        except Exception:
            ok = False
        return {"host": name, "address": addr, "ok": ok}

    workers = min(SEND_SETTINGS["max_workers"], len(hosts))
    results = [None] * len(hosts)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_idx = {
            pool.submit(send_one, h): i for i, h in enumerate(hosts)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception:
                h = hosts[idx]
                results[idx] = {
                    "host":    h.get("name", h.get("address", "")),
                    "address": h.get("address", ""),
                    "ok":      False,
                }

    # Записать в историю
    hist = jread(HISTORY_FILE)
    hist.insert(0, {
        "id":      int(datetime.now().timestamp() * 1000),
        "time":    datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        "title":   title,
        "level":   level,
        "type":    msg_type,
        "total":   len(results),
        "ok":      sum(1 for r in results if r["ok"]),
        "results": results,
    })
    jwrite(HISTORY_FILE, hist[:200])
    return results


# ─────────────── Страница логина ───────────────
def login_page(error: str = "") -> str:
    err = f'<div class="err">{error}</div>' if error else ""
    return f"""<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="UTF-8"><title>Notify Admin — Вход</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0f1117;font-family:'Segoe UI',Arial,sans-serif;
     display:flex;align-items:center;justify-content:center;min-height:100vh;color:#e8eaf0}}
.box{{background:#171b26;border:1px solid #2a3050;border-radius:16px;padding:44px;width:360px}}
.logo{{width:52px;height:52px;background:#4f8fff;border-radius:14px;display:flex;
       align-items:center;justify-content:center;font-size:26px;margin:0 auto 22px}}
h1{{text-align:center;font-size:20px;font-weight:800;margin-bottom:5px}}
.sub{{text-align:center;color:#4a5270;font-size:12px;margin-bottom:30px}}
label{{font-size:12px;font-weight:600;color:#8891aa;display:block;margin-bottom:6px}}
input{{width:100%;background:#0f1117;border:1px solid #2a3050;border-radius:8px;
       color:#e8eaf0;font-size:14px;padding:11px 14px;outline:none;
       margin-bottom:18px;font-family:inherit;transition:border-color .2s}}
input:focus{{border-color:#4f8fff}}
button{{width:100%;background:#4f8fff;border:none;border-radius:8px;
        color:white;font-size:14px;font-weight:700;padding:13px;
        cursor:pointer;font-family:inherit}}
button:hover{{background:#3a7bff}}
.err{{background:rgba(255,107,107,.1);border:1px solid rgba(255,107,107,.3);
      color:#ff6b6b;border-radius:8px;padding:11px 14px;
      font-size:13px;margin-bottom:18px;text-align:center}}
</style></head><body>
<div class="box">
  <div class="logo">📢</div>
  <h1>Notify Admin</h1>
  <div class="sub">Система экстренного оповещения</div>
  {err}
  <form method="POST" action="/login">
    <label>Логин</label>
    <input type="text" name="login" autofocus autocomplete="username">
    <label>Пароль</label>
    <input type="password" name="password" autocomplete="current-password">
    <button type="submit">Войти →</button>
  </form>
</div></body></html>"""


# ─────────────── HTTP Handler ───────────────
class Handler(BaseHTTPRequestHandler):

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, content, status=200, extra=None):
        body = content if isinstance(content, bytes) else content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, loc, extra=None):
        self.send_response(302)
        self.send_header("Location", loc)
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()

    def read_body(self):
        n = int(self.headers.get("Content-Length", 0))
        if n == 0:
            return {}
        raw = self.rfile.read(n)
        ct  = self.headers.get("Content-Type", "")
        try:
            if "json" in ct:
                return json.loads(raw.decode("utf-8"))
            if "urlencoded" in ct:
                return {k: v[0] for k, v in
                        parse_qs(raw.decode("utf-8"), keep_blank_values=True).items()}
        except Exception:
            pass
        return {}

    def authed(self):
        return session_valid(get_session(self.headers))

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/login":
            self.send_html(login_page()); return
        if not self.authed():
            self.redirect("/login"); return
        if p in ("/", "/index.html"):
            self.send_html(INDEX_FILE.read_bytes())
        elif p == "/api/hosts":
            self.send_json(jread(HOSTS_FILE))
        elif p == "/api/templates":
            self.send_json(jread(TEMPLATES_FILE))
        elif p == "/api/history":
            self.send_json(jread(HISTORY_FILE))
        elif p == "/api/settings/get":
            self.send_json({
                "ok":            True,
                "max_workers":   SEND_SETTINGS["max_workers"],
                "send_timeout":  SEND_SETTINGS["send_timeout"],
                "ansible_api_key": ANSIBLE_SETTINGS["api_key"],
            })
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        p    = urlparse(self.path).path
        data = self.read_body()

        if p == "/login":
            if (data.get("login","").strip()    == ADMIN_LOGIN and
                data.get("password","").strip() == ADMIN_PASSWORD):
                tok = session_create()
                self.redirect("/", {"Set-Cookie":
                    f"session={tok}; HttpOnly; Path=/; Max-Age={SESSION_TTL}"})
            else:
                self.send_html(login_page("Неверный логин или пароль"))
            return

        if p == "/logout":
            _sessions.pop(get_session(self.headers), None)
            self.redirect("/login", {"Set-Cookie": "session=; Max-Age=0; Path=/"})
            return

        # /api/ansible использует X-API-Key — не требует браузерной сессии
        if p == "/api/ansible":
            api_key = self.headers.get("X-API-Key", "")
            if api_key != ANSIBLE_SETTINGS["api_key"]:
                self.send_json({"ok": False, "error": "Invalid API key"}, 403)
                return

            title    = (data.get("title")   or "").strip()
            body     = (data.get("message") or "").strip()
            level    = (data.get("level")   or "info")
            msg_type = (data.get("type")    or "popup")
            timeout  =  data.get("timeout", 8)
            hosts_raw = data.get("hosts")  or []

            if not title:
                self.send_json({"ok": False, "error": "Заголовок пустой"}, 400); return
            if not hosts_raw:
                self.send_json({"ok": False, "error": "Нет хостов"}, 400); return

            hosts = []
            for h in hosts_raw:
                if isinstance(h, str):
                    hosts.append({"name": h, "address": h})
                elif isinstance(h, dict):
                    hosts.append(h)

            results = do_notify_ansible(title, body, level, msg_type, timeout, hosts)
            self.send_json({
                "ok":        True,
                "results":   results,
                "delivered": sum(1 for r in results if r["ok"]),
                "total":     len(results),
            })
            return

        if not self.authed():
            self.send_json({"ok": False, "error": "Unauthorized"}, 401); return

        if p == "/api/hosts/save":
            if not isinstance(data, list):
                self.send_json({"ok": False, "error": "Ожидается список"}, 400); return
            jwrite(HOSTS_FILE, data)
            self.send_json({"ok": True, "count": len(data)})

        elif p == "/api/templates/save":
            if not isinstance(data, list):
                self.send_json({"ok": False, "error": "Ожидается список"}, 400); return
            jwrite(TEMPLATES_FILE, data)
            self.send_json({"ok": True, "count": len(data)})

        elif p == "/api/send":
            title    = (data.get("title")   or "").strip()
            body     = (data.get("message") or "").strip()
            level    = (data.get("level")   or "info")
            msg_type = (data.get("type")    or "")
            timeout  =  int(data.get("timeout") or 0)
            hosts    =  data.get("hosts")   or []
            if not title:
                self.send_json({"ok": False, "error": "Заголовок пустой"}, 400); return
            if not hosts:
                self.send_json({"ok": False, "error": "Нет хостов"}, 400); return
            results = do_notify(title, body, level, hosts, msg_type, timeout)
            self.send_json({
                "ok":       True,
                "results":  results,
                "delivered": sum(1 for r in results if r["ok"]),
                "total":    len(results),
            })

        elif p == "/api/settings/save":
            try:
                w = int(data.get("max_workers",  SEND_SETTINGS["max_workers"]))
                t = int(data.get("send_timeout", SEND_SETTINGS["send_timeout"]))
                if w < 1 or w > 500:
                    raise ValueError("max_workers должен быть от 1 до 500")
                if t < 1 or t > 30:
                    raise ValueError("send_timeout должен быть от 1 до 30")
                SEND_SETTINGS["max_workers"]  = w
                SEND_SETTINGS["send_timeout"] = t
                self.send_json({"ok": True, "max_workers": w, "send_timeout": t})
            except (ValueError, TypeError) as e:
                self.send_json({"ok": False, "error": str(e)}, 400)

        elif p == "/api/settings/save-apikey":
            key = (data.get("ansible_api_key") or "").strip()
            if not key:
                self.send_json({"ok": False, "error": "Ключ пустой"}, 400); return
            ANSIBLE_SETTINGS["api_key"] = key
            self.send_json({"ok": True})


        else:
            self.send_response(404); self.end_headers()

    def log_message(self, fmt, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]}")


# ─────────────── Запуск ───────────────
if __name__ == "__main__":
    init_db()
    print("╔══════════════════════════════════════╗")
    print("║       Notify Admin Panel             ║")
    print(f"║   http://localhost:{PANEL_PORT}              ║")
    print(f"║   Логин:  {ADMIN_LOGIN:<10}               ║")
    print(f"║   Пароль: {ADMIN_PASSWORD:<10}               ║")
    print("║   Ctrl+C — остановить                ║")
    print("╚══════════════════════════════════════╝")
    srv = HTTPServer((PANEL_HOST, PANEL_PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлен.")
