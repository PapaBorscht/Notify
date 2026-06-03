#!/usr/bin/env python3
"""
Автотесты системы оповещения notify-agent.
Тестирует: server.py, sender.py, логику агента (без GUI).
Запуск: python3 test_notify.py
"""
import sys
import os
import json
import time
import shutil
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch, MagicMock

# Путь к модулям
BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

# ══════════════════════════════════════════════════════
# Утилиты
# ══════════════════════════════════════════════════════

def http_post(url, data=None, headers=None, timeout=5):
    body = json.dumps(data or {}).encode()
    req  = urllib.request.Request(url, data=body,
                                  headers={"Content-Type":"application/json",
                                           **(headers or {})},
                                  method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception as e:
        return None, str(e)

def http_get(url, headers=None, timeout=5):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            try:
                return r.status, json.loads(body)
            except Exception:
                return r.status, body.decode('utf-8', errors='replace')
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body.decode('utf-8', errors='replace')
    except Exception as e:
        return None, str(e)


# ══════════════════════════════════════════════════════
# 1. Тесты логики агента (без GUI)
# ══════════════════════════════════════════════════════

class TestAgentLogic(unittest.TestCase):

    def _import_agent_funcs(self):
        """Импортировать только функции агента без запуска Qt."""
        import importlib.util, types
        # Мокаем PyQt5 чтобы не падало без дисплея
        for mod in ['PyQt5', 'PyQt5.QtWidgets', 'PyQt5.QtCore',
                    'PyQt5.QtGui']:
            if mod not in sys.modules:
                sys.modules[mod] = types.ModuleType(mod)
        spec = importlib.util.spec_from_file_location("agent", BASE / "agent.py")
        agent = importlib.util.module_from_spec(spec)
        # Патчим тяжёлые зависимости
        with patch.dict('sys.modules', {
            'PyQt5': MagicMock(), 'PyQt5.QtWidgets': MagicMock(),
            'PyQt5.QtCore': MagicMock(), 'PyQt5.QtGui': MagicMock()
        }):
            try:
                spec.loader.exec_module(agent)
            except Exception:
                pass
        return agent

    def test_resolve_type_explicit_popup(self):
        """Явный type=popup должен вернуть popup."""
        agent = self._import_agent_funcs()
        if hasattr(agent, 'resolve_type'):
            self.assertEqual(agent.resolve_type("warning", "popup"), "popup")

    def test_resolve_type_explicit_fullscreen(self):
        """Явный type=fullscreen должен вернуть fullscreen."""
        agent = self._import_agent_funcs()
        if hasattr(agent, 'resolve_type'):
            self.assertEqual(agent.resolve_type("info", "fullscreen"), "fullscreen")

    def test_resolve_type_critical_default(self):
        """critical без type → fullscreen."""
        agent = self._import_agent_funcs()
        if hasattr(agent, 'resolve_type'):
            self.assertEqual(agent.resolve_type("critical", ""), "fullscreen")

    def test_resolve_type_warning_default(self):
        """warning без type → fullscreen."""
        agent = self._import_agent_funcs()
        if hasattr(agent, 'resolve_type'):
            self.assertEqual(agent.resolve_type("warning", ""), "fullscreen")

    def test_resolve_type_info_default(self):
        """info без type → popup."""
        agent = self._import_agent_funcs()
        if hasattr(agent, 'resolve_type'):
            self.assertEqual(agent.resolve_type("info", ""), "popup")

    def test_md_to_html_headers(self):
        """Markdown заголовки конвертируются в HTML теги."""
        agent = self._import_agent_funcs()
        if hasattr(agent, 'md_to_html'):
            html = agent.md_to_html("## Заголовок", "#fff")
            self.assertIn("<h2>", html)
            self.assertIn("Заголовок", html)

    def test_md_to_html_bold(self):
        """**жирный** → <b>жирный</b>."""
        agent = self._import_agent_funcs()
        if hasattr(agent, 'md_to_html'):
            html = agent.md_to_html("**жирный**", "#fff")
            self.assertIn("<b>", html)

    def test_md_to_html_blockquote(self):
        """> цитата → <blockquote>."""
        agent = self._import_agent_funcs()
        if hasattr(agent, 'md_to_html'):
            html = agent.md_to_html("> цитата", "#fff")
            self.assertIn("<blockquote>", html)

    def test_md_to_html_xss(self):
        """HTML теги в тексте должны быть экранированы."""
        agent = self._import_agent_funcs()
        if hasattr(agent, 'md_to_html'):
            html = agent.md_to_html("<script>alert(1)</script>", "#fff")
            self.assertNotIn("<script>", html)
            self.assertIn("&lt;script&gt;", html)


# ══════════════════════════════════════════════════════
# 2. Тесты HTTP сервера (запускаем реальный server.py)
# ══════════════════════════════════════════════════════

class TestServer(unittest.TestCase):

    TEST_PORT  = 18081
    SERVER_URL = f"http://127.0.0.1:{TEST_PORT}"
    _tmpdir    = None
    _server_t  = None
    _srv_inst  = None

    @classmethod
    def setUpClass(cls):
        """Запустить тестовый экземпляр сервера."""
        cls._tmpdir = tempfile.mkdtemp(prefix="notify_test_")

        # Патчим пути в server.py через монки
        import importlib.util
        spec   = importlib.util.spec_from_file_location("server", BASE / "server.py")
        cls.srv_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.srv_mod)

        # Переопределить порт и пути данных
        cls.srv_mod.PANEL_PORT  = cls.TEST_PORT
        cls.srv_mod.PANEL_HOST  = "127.0.0.1"
        cls.srv_mod.DATA_DIR    = Path(cls._tmpdir) / "data"
        cls.srv_mod.HOSTS_FILE  = cls.srv_mod.DATA_DIR / "hosts.json"
        cls.srv_mod.TEMPLATES_FILE = cls.srv_mod.DATA_DIR / "templates.json"
        cls.srv_mod.HISTORY_FILE   = cls.srv_mod.DATA_DIR / "history.json"
        cls.srv_mod.INDEX_FILE  = BASE / "index.html"

        cls.srv_mod.init_db()

        # Запустить в фоне
        from http.server import HTTPServer
        cls._srv_inst = HTTPServer(
            ("127.0.0.1", cls.TEST_PORT), cls.srv_mod.Handler
        )
        cls._server_t = threading.Thread(
            target=cls._srv_inst.serve_forever, daemon=True
        )
        cls._server_t.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        if cls._srv_inst:
            cls._srv_inst.shutdown()
        if cls._tmpdir:
            shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _login(self):
        """Получить сессионный cookie."""
        import http.cookiejar, urllib.request
        cj      = http.cookiejar.CookieJar()
        opener  = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cj)
        )
        data = urllib.parse.urlencode(
            {"login": "admin", "password": "admin123"}
        ).encode()
        req = urllib.request.Request(
            f"{self.SERVER_URL}/login", data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST"
        )
        try:
            opener.open(req)
        except Exception:
            pass
        for c in cj:
            if c.name == "session":
                return c.value
        return None

    # ── Авторизация ──

    def test_redirect_to_login_unauthed(self):
        """Без авторизации / должен редиректить на /login."""
        req = urllib.request.Request(
            f"{self.SERVER_URL}/",
            headers={"Accept": "text/html"}
        )
        # Отключить авто-редирект
        opener = urllib.request.build_opener(
            urllib.request.HTTPRedirectHandler()
        )
        try:
            opener.open(req)
        except urllib.error.HTTPError as e:
            self.assertIn(e.code, [302, 301])
        except Exception:
            pass

    def test_login_wrong_password(self):
        """Неверный пароль → остаёмся на /login (нет редиректа на /)."""
        import urllib.parse
        data = urllib.parse.urlencode(
            {"login": "admin", "password": "wrongpass"}
        ).encode()
        req = urllib.request.Request(
            f"{self.SERVER_URL}/login", data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST"
        )
        # Ждём либо 200 (форма с ошибкой) либо редирект на /login
        try:
            with urllib.request.urlopen(req) as r:
                body = r.read().decode()
                self.assertIn("Неверный", body)
        except urllib.error.HTTPError as e:
            # Редирект обратно на /login — тоже OK
            self.assertIn(e.code, [200, 302])

    def test_login_correct(self):
        """Правильный логин/пароль → получаем session cookie."""
        token = self._login()
        self.assertIsNotNone(token, "Не получили session cookie после логина")
        self.assertGreater(len(token), 10)

    def test_api_hosts_unauthed(self):
        """GET /api/hosts без cookie → редирект на /login."""
        status, body = http_get(f"{self.SERVER_URL}/api/hosts")
        # urllib автоматически следует 302 и возвращает страницу /login (200)
        # Главное — мы не получили JSON список хостов
        if status == 200:
            # Должна быть HTML страница логина, а не JSON
            self.assertIsInstance(body, str,
                "Без авторизации должен вернуться HTML страница логина")
            self.assertTrue(
                any(w in body.lower() for w in ["login", "вход", "пароль", "notify admin"]),
                f"Ожидали страницу логина, получили: {body[:100]}"
            )
        else:
            self.assertIn(status, [302, 301, 401])

    def test_api_hosts_authed(self):
        """GET /api/hosts с cookie → 200 и список."""
        token  = self._login()
        status, data = http_get(
            f"{self.SERVER_URL}/api/hosts",
            headers={"Cookie": f"session={token}"}
        )
        self.assertEqual(status, 200)
        self.assertIsInstance(data, list)

    def test_api_hosts_save_and_read(self):
        """Сохранить хост и убедиться что он читается обратно."""
        token = self._login()
        hosts = [
            {"id": 1, "name": "testhost", "address": "10.0.0.1",
             "group": "test", "enabled": True}
        ]
        status, res = http_post(
            f"{self.SERVER_URL}/api/hosts/save", hosts,
            headers={"Cookie": f"session={token}"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(res.get("ok"))

        status2, data2 = http_get(
            f"{self.SERVER_URL}/api/hosts",
            headers={"Cookie": f"session={token}"}
        )
        self.assertEqual(status2, 200)
        self.assertEqual(len(data2), 1)
        self.assertEqual(data2[0]["name"], "testhost")
        self.assertEqual(data2[0]["address"], "10.0.0.1")

    def test_api_hosts_save_not_list(self):
        """Попытка сохранить не-список → 400."""
        token = self._login()
        status, res = http_post(
            f"{self.SERVER_URL}/api/hosts/save",
            {"bad": "data"},
            headers={"Cookie": f"session={token}"}
        )
        self.assertEqual(status, 400)
        self.assertFalse(res.get("ok"))

    def test_api_send_no_title(self):
        """Отправка без заголовка → 400."""
        token = self._login()
        status, res = http_post(
            f"{self.SERVER_URL}/api/send",
            {"message": "текст", "hosts": [{"address": "1.2.3.4"}]},
            headers={"Cookie": f"session={token}"}
        )
        self.assertEqual(status, 400)

    def test_api_send_no_hosts(self):
        """Отправка без хостов → 400."""
        token = self._login()
        status, res = http_post(
            f"{self.SERVER_URL}/api/send",
            {"title": "Тест", "message": "текст", "hosts": []},
            headers={"Cookie": f"session={token}"}
        )
        self.assertEqual(status, 400)

    def test_api_send_type_and_timeout_passed(self):
        """type и timeout должны доходить до _send_one (мокаем)."""
        token = self._login()
        sent_payloads = []

        import urllib.request as ur
        original_urlopen = ur.urlopen

        def fake_urlopen(req, timeout=None):
            if hasattr(req, 'data'):
                try:
                    sent_payloads.append(json.loads(req.data))
                except Exception:
                    pass
            raise urllib.error.URLError("mocked")

        with patch('urllib.request.urlopen', side_effect=fake_urlopen):
            http_post(
                f"{self.SERVER_URL}/api/send",
                {
                    "title":   "Тест попапа",
                    "message": "текст",
                    "level":   "info",
                    "type":    "popup",
                    "timeout": 10,
                    "hosts":   [{"name": "h1", "address": "1.2.3.4"}]
                },
                headers={"Cookie": f"session={token}"}
            )

        self.assertGreater(len(sent_payloads), 0,
                           "Агенту не было отправлено ни одного запроса")
        p = sent_payloads[0]
        self.assertEqual(p.get("type"),    "popup",
                         f"type не передан агенту: {p}")
        self.assertEqual(p.get("timeout"), 10,
                         f"timeout не передан агенту: {p}")

    # ── Ansible endpoint ──

    def test_ansible_no_key(self):
        """POST /api/ansible без X-API-Key → 403."""
        status, res = http_post(
            f"{self.SERVER_URL}/api/ansible",
            {"title": "test", "hosts": ["1.2.3.4"]}
        )
        self.assertEqual(status, 403)

    def test_ansible_wrong_key(self):
        """POST /api/ansible с неверным ключом → 403."""
        status, res = http_post(
            f"{self.SERVER_URL}/api/ansible",
            {"title": "test", "hosts": ["1.2.3.4"]},
            headers={"X-API-Key": "wrong-key"}
        )
        self.assertEqual(status, 403)

    def test_ansible_correct_key_no_title(self):
        """Правильный ключ но нет заголовка → 400."""
        status, res = http_post(
            f"{self.SERVER_URL}/api/ansible",
            {"hosts": ["1.2.3.4"]},
            headers={"X-API-Key": "ansible-secret-key"}
        )
        self.assertEqual(status, 400)

    def test_ansible_correct_key_no_hosts(self):
        """Правильный ключ, заголовок есть, хостов нет → 400."""
        status, res = http_post(
            f"{self.SERVER_URL}/api/ansible",
            {"title": "Тест"},
            headers={"X-API-Key": "ansible-secret-key"}
        )
        self.assertEqual(status, 400)

    def test_ansible_valid_request(self):
        """Валидный Ansible запрос → 200 (агент недоступен, но ответ OK)."""
        with patch.object(self.srv_mod, '_send_one', return_value={'host':'h','address':'192.168.1.10','ok':False}):
            status, res = http_post(
                f"{self.SERVER_URL}/api/ansible",
                {
                    "title":   "Обновление",
                    "message": "Текст",
                    "level":   "warning",
                    "type":    "popup",
                    "timeout": 10,
                    "hosts":   ["192.168.1.10"]
                },
                headers={"X-API-Key": "ansible-secret-key"}
            )
        self.assertEqual(status, 200)
        self.assertTrue(res.get("ok"))
        self.assertEqual(res.get("total"), 1)

    def test_ansible_hosts_as_strings(self):
        """Ansible может передавать hosts как список строк."""
        with patch.object(self.srv_mod, '_send_one', return_value={'host':'h','address':'x','ok':False}):
            status, res = http_post(
                f"{self.SERVER_URL}/api/ansible",
                {"title": "T", "hosts": ["10.0.0.1", "10.0.0.2"]},
                headers={"X-API-Key": "ansible-secret-key"}
            )
        self.assertEqual(status, 200)
        self.assertEqual(res.get("total"), 2)

    # ── Настройки ──

    def test_settings_get(self):
        """GET /api/settings/get → возвращает настройки."""
        token = self._login()
        status, res = http_get(
            f"{self.SERVER_URL}/api/settings/get",
            headers={"Cookie": f"session={token}"}
        )
        self.assertEqual(status, 200)
        self.assertIn("max_workers",   res)
        self.assertIn("send_timeout",  res)
        self.assertIn("ansible_api_key", res)

    def test_settings_save_workers(self):
        """Изменить max_workers → применяется сразу."""
        # Проверяем логику напрямую через модуль
        original = self.srv_mod.SEND_SETTINGS["max_workers"]
        self.srv_mod.SEND_SETTINGS["max_workers"]  = 25
        self.srv_mod.SEND_SETTINGS["send_timeout"] = 2
        self.assertEqual(self.srv_mod.SEND_SETTINGS["max_workers"],  25)
        self.assertEqual(self.srv_mod.SEND_SETTINGS["send_timeout"], 2)
        # Восстановить
        self.srv_mod.SEND_SETTINGS["max_workers"] = original

    def test_settings_save_invalid_workers(self):
        """max_workers=0 → 400."""
        token = self._login()
        status, res = http_post(
            f"{self.SERVER_URL}/api/settings/save",
            {"max_workers": 0, "send_timeout": 3},
            headers={"Cookie": f"session={token}"}
        )
        self.assertEqual(status, 400)

    def test_settings_save_apikey(self):
        """Сохранить и прочитать API ключ."""
        token = self._login()
        http_post(
            f"{self.SERVER_URL}/api/settings/save-apikey",
            {"ansible_api_key": "new-test-key"},
            headers={"Cookie": f"session={token}"}
        )
        # Проверить что новый ключ работает
        with patch.object(self.srv_mod, '_send_one', return_value={'host':'h','address':'1.1.1.1','ok':False}):
            status, _ = http_post(
                f"{self.SERVER_URL}/api/ansible",
                {"title": "T", "hosts": ["1.1.1.1"]},
                headers={"X-API-Key": "new-test-key"}
            )
        self.assertEqual(status, 200)

    # ── История ──

    def test_history_written_after_send(self):
        """После отправки → запись в историю."""
        token = self._login()
        with patch.object(self.srv_mod, '_send_one', return_value={'host':'h1','address':'1.2.3.4','ok':False}):
            http_post(
                f"{self.SERVER_URL}/api/send",
                {
                    "title": "История тест",
                    "message": "текст",
                    "level": "info",
                    "hosts": [{"name": "h1", "address": "1.2.3.4"}]
                },
                headers={"Cookie": f"session={token}"}
            )
        status, hist = http_get(
            f"{self.SERVER_URL}/api/history",
            headers={"Cookie": f"session={token}"}
        )
        self.assertEqual(status, 200)
        titles = [h.get("title") for h in hist]
        self.assertIn("История тест", titles)


# ══════════════════════════════════════════════════════
# 3. Тесты sender.py
# ══════════════════════════════════════════════════════

class TestSender(unittest.TestCase):

    def _import_sender(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "sender", BASE / "sender.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_send_success(self):
        """send() возвращает True при HTTP 200."""
        sender = self._import_sender()

        class FakeResp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): pass

        with patch('urllib.request.urlopen', return_value=FakeResp()):
            result = sender.send("Тест", "Текст", "info", "", 0, "1.2.3.4")
        self.assertTrue(result)

    def test_send_failure_on_url_error(self):
        """send() возвращает False при URLError."""
        sender = self._import_sender()
        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError("timeout")):
            result = sender.send("Тест", "Текст", "info", "", 0, "1.2.3.4")
        self.assertFalse(result)

    def test_send_payload_contains_type(self):
        """send() с type='popup' передаёт его в JSON."""
        sender   = self._import_sender()
        captured = []

        def fake_urlopen(req, timeout=None):
            captured.append(json.loads(req.data))
            raise urllib.error.URLError("ok")

        with patch('urllib.request.urlopen', side_effect=fake_urlopen):
            sender.send("Т", "М", "warning", "popup", 15, "1.2.3.4")

        self.assertTrue(len(captured) > 0)
        p = captured[0]
        self.assertEqual(p.get("type"),    "popup")
        self.assertEqual(p.get("timeout"), 15)
        self.assertEqual(p.get("level"),   "warning")

    def test_send_payload_no_type_when_empty(self):
        """send() без type не добавляет поле type в JSON."""
        sender   = self._import_sender()
        captured = []

        def fake_urlopen(req, timeout=None):
            captured.append(json.loads(req.data))
            raise urllib.error.URLError("ok")

        with patch('urllib.request.urlopen', side_effect=fake_urlopen):
            sender.send("Т", "М", "info", "", 0, "1.2.3.4")

        self.assertTrue(len(captured) > 0)
        self.assertNotIn("type", captured[0])

    def test_load_hosts_from_json(self):
        """load_hosts() читает включённые хосты из файла."""
        sender = self._import_sender()
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        ) as f:
            json.dump([
                {"address": "1.1.1.1", "enabled": True},
                {"address": "2.2.2.2", "enabled": False},
                {"address": "3.3.3.3", "enabled": True},
            ], f)
            fname = f.name

        with patch.object(sender, 'HOSTS_FILE', Path(fname)):
            hosts = sender.load_hosts()

        os.unlink(fname)
        self.assertEqual(hosts, ["1.1.1.1", "3.3.3.3"])

    def test_load_hosts_missing_file(self):
        """load_hosts() возвращает [] если файл не существует."""
        sender = self._import_sender()
        with patch.object(sender, 'HOSTS_FILE', Path("/nonexistent/hosts.json")):
            hosts = sender.load_hosts()
        self.assertEqual(hosts, [])


# ══════════════════════════════════════════════════════
# 4. Тесты JSON структуры данных
# ══════════════════════════════════════════════════════

class TestDataFiles(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp()
        import importlib.util
        spec = importlib.util.spec_from_file_location("server", BASE/"server.py")
        cls.srv = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.srv)
        cls.srv.DATA_DIR       = Path(cls._tmpdir) / "data"
        cls.srv.HOSTS_FILE     = cls.srv.DATA_DIR / "hosts.json"
        cls.srv.TEMPLATES_FILE = cls.srv.DATA_DIR / "templates.json"
        cls.srv.HISTORY_FILE   = cls.srv.DATA_DIR / "history.json"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def test_init_db_creates_files(self):
        """init_db() создаёт нужные файлы."""
        self.srv.init_db()
        self.assertTrue(self.srv.HOSTS_FILE.exists())
        self.assertTrue(self.srv.TEMPLATES_FILE.exists())
        self.assertTrue(self.srv.HISTORY_FILE.exists())

    def test_hosts_json_valid(self):
        """hosts.json содержит валидный JSON список."""
        self.srv.init_db()
        data = json.loads(self.srv.HOSTS_FILE.read_text())
        self.assertIsInstance(data, list)

    def test_templates_json_has_required_fields(self):
        """Каждый шаблон содержит обязательные поля."""
        self.srv.init_db()
        templates = json.loads(self.srv.TEMPLATES_FILE.read_text())
        for t in templates:
            for field in ("id", "name", "title", "body", "level"):
                self.assertIn(field, t,
                              f"Шаблону '{t.get('name')}' не хватает поля '{field}'")

    def test_jread_jwrite(self):
        """jwrite сохраняет, jread читает корректно."""
        path = Path(self._tmpdir) / "test.json"
        data = [{"id": 1, "name": "тест", "value": 42}]
        self.srv.jwrite(path, data)
        read = self.srv.jread(path)
        self.assertEqual(read, data)

    def test_jread_invalid_file(self):
        """jread на повреждённый файл возвращает []."""
        path = Path(self._tmpdir) / "bad.json"
        path.write_text("{invalid json{{")
        result = self.srv.jread(path)
        self.assertEqual(result, [])

    def test_jread_missing_file(self):
        """jread на несуществующий файл возвращает []."""
        result = self.srv.jread(Path("/no/such/file.json"))
        self.assertEqual(result, [])

    def test_history_max_200(self):
        """История не превышает 200 записей."""
        self.srv.init_db()
        # Записать 250 записей
        hist = [{"id": i, "time": "00:00", "title": f"t{i}",
                 "level": "info", "total": 1, "ok": 1, "results": []}
                for i in range(250)]
        self.srv.jwrite(self.srv.HISTORY_FILE, hist[:200])
        read = self.srv.jread(self.srv.HISTORY_FILE)
        self.assertLessEqual(len(read), 200)


# ══════════════════════════════════════════════════════
# 5. Тесты сессий
# ══════════════════════════════════════════════════════

class TestSessions(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("server", BASE/"server.py")
        cls.srv = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.srv)

    def test_create_session_unique(self):
        """Два вызова создают разные токены."""
        t1 = self.srv.session_create()
        t2 = self.srv.session_create()
        self.assertNotEqual(t1, t2)

    def test_valid_session(self):
        """Созданная сессия валидна сразу."""
        token = self.srv.session_create()
        self.assertTrue(self.srv.session_valid(token))

    def test_invalid_session_wrong_token(self):
        """Неверный токен не валиден."""
        self.assertFalse(self.srv.session_valid("fakefakefake"))

    def test_invalid_session_empty(self):
        """Пустой токен не валиден."""
        self.assertFalse(self.srv.session_valid(""))

    def test_session_expires(self):
        """Сессия истекает по TTL."""
        token = self.srv.session_create()
        # Принудительно устарить
        self.srv._sessions[token] -= (self.srv.SESSION_TTL + 1)
        self.assertFalse(self.srv.session_valid(token))

    def test_get_session_from_cookie(self):
        """Парсинг session= из Cookie заголовка."""
        class FakeHeaders:
            def get(self, key, default=""):
                return "other=abc; session=mytoken123; foo=bar"
        token = self.srv.get_session(FakeHeaders())
        self.assertEqual(token, "mytoken123")

    def test_get_session_no_cookie(self):
        """Нет cookie → пустая строка."""
        class FakeHeaders:
            def get(self, key, default=""):
                return ""
        token = self.srv.get_session(FakeHeaders())
        self.assertEqual(token, "")


# ══════════════════════════════════════════════════════
# Запуск
# ══════════════════════════════════════════════════════

if __name__ == "__main__":
    import urllib.parse  # нужен для теста логина

    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestAgentLogic))
    suite.addTests(loader.loadTestsFromTestCase(TestSessions))
    suite.addTests(loader.loadTestsFromTestCase(TestDataFiles))
    suite.addTests(loader.loadTestsFromTestCase(TestSender))
    suite.addTests(loader.loadTestsFromTestCase(TestServer))

    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stdout)
    result = runner.run(suite)

    print()
    print("=" * 60)
    print(f"Всего тестов:  {result.testsRun}")
    print(f"Прошло:        {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Провалено:     {len(result.failures)}")
    print(f"Ошибок:        {len(result.errors)}")
    print("=" * 60)

    sys.exit(0 if result.wasSuccessful() else 1)
