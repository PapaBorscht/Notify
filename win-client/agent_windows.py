#!/usr/bin/env python3
"""
Notify Agent v3.0 — Windows 10/11
Два типа уведомлений:
  - fullscreen: полный экран, блокирует работу (тревога, атаки)
  - popup:      маленькое окно снизу справа, не мешает работе (Ansible, события)

Логика выбора типа:
  - Если передан type="fullscreen" → всегда полный экран
  - Если передан type="popup"      → всегда попап
  - Если type не передан:
      critical → fullscreen
      warning  → fullscreen
      info     → popup
"""

import os
import sys
import re
import json
import logging
import threading
import socket
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

from PyQt5.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QAction,
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTextBrowser, QDesktopWidget, QProgressBar
)
from PyQt5.QtCore  import Qt, pyqtSignal, QObject, QTimer, pyqtSlot
from PyQt5.QtGui   import QFont, QIcon, QColor, QPixmap, QPainter

PORT      = 9988
TOKEN     = "supersecrettoken123"
VERSION   = "3.0"

# ── Windows-пути: %LOCALAPPDATA%\NotifyAgent\ ──
APPDATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "NotifyAgent")
os.makedirs(APPDATA_DIR, exist_ok=True)

LOCK_FILE = os.path.join(APPDATA_DIR, "notify-agent.lock")
LOG_FILE  = os.path.join(APPDATA_DIR, f"notify-agent-{os.environ.get('USERNAME', 'unknown')}.log")

LEVELS = {
    "info":     {"bg": "#1a1f3a", "accent": "#4f8fff", "label": "Информация"},
    "warning":  {"bg": "#2a1f0a", "accent": "#ffb347", "label": "Предупреждение"},
    "critical": {"bg": "#2a0a0a", "accent": "#ff4444", "label": "⚠ ТРЕВОГА"},
}

POPUP_DEFAULT_TIMEOUT = 8   # секунд до автозакрытия попапа
_notifier  = None
_tray_app  = None
_popups    = []   # список активных попапов для стека


# ─── Логирование ───
def setup_log():
    lg = logging.getLogger("agent")
    if lg.handlers:
        return lg

    # Ротация: если лог > 5МБ — архивировать и начать новый
    log_path = LOG_FILE
    try:
        if os.path.getsize(log_path) > 5 * 1024 * 1024:
            import shutil
            shutil.move(log_path, log_path + ".old")
    except OSError:
        pass

    fmt = logging.Formatter("[%(asctime)s] %(levelname)-8s %(message)s",
                            datefmt="%H:%M:%S")
    lg.setLevel(logging.DEBUG)
    # Отдельный файл для каждого PID чтобы видеть двойные запуски
    fh = logging.FileHandler(log_path, encoding="utf-8", mode="a")
    fh.setFormatter(fmt)
    lg.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    lg.addHandler(sh)
    lg.propagate = False   # не передавать в root logger — предотвращает дублирование
    return lg

log = setup_log()


def _install_hooks():
    """
    Максимальное логирование: перехватить ВСЕ необработанные исключения,
    сигналы краша и сообщения Qt.
    """
    import traceback
    import signal
    import platform

    # 1. Перехват необработанных исключений Python
    def _excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, (SystemExit, KeyboardInterrupt)):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.critical(
            "НЕОБРАБОТАННОЕ ИСКЛЮЧЕНИЕ:\n" +
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        )
    sys.excepthook = _excepthook

    # 2. Перехват исключений в потоках
    def _thread_excepthook(args):
        log.critical(
            f"ИСКЛЮЧЕНИЕ В ПОТОКЕ {args.thread.name}:\n" +
            "".join(traceback.format_exception(
                args.exc_type, args.exc_value, args.exc_traceback
            ))
        )
    threading.excepthook = _thread_excepthook

    # 3. Сигналы — на Windows доступны только SIGTERM/SIGINT/SIGBREAK
    def _sig_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        log.warning(f"Получен сигнал {sig_name} (#{signum}) — агент завершается")
        import traceback as tb
        log.debug("Стек в момент сигнала:\n" + "".join(tb.format_stack(frame)))
        if signum == signal.SIGTERM:
            sys.exit(0)

    sig_list = [signal.SIGTERM, signal.SIGINT]
    if hasattr(signal, "SIGBREAK"):       # Windows-специфичный сигнал
        sig_list.append(signal.SIGBREAK)
    for sig in sig_list:
        try:
            signal.signal(sig, _sig_handler)
        except Exception:
            pass

    # 4. Qt сообщения — предупреждения и критические ошибки Qt
    from PyQt5.QtCore import qInstallMessageHandler, QtMsgType
    def _qt_msg_handler(msg_type, context, message):
        if msg_type == QtMsgType.QtDebugMsg:
            log.debug(f"Qt: {message}")
        elif msg_type == QtMsgType.QtInfoMsg:
            log.info(f"Qt: {message}")
        elif msg_type == QtMsgType.QtWarningMsg:
            log.warning(f"Qt WARNING: {message}")
        elif msg_type == QtMsgType.QtCriticalMsg:
            log.error(f"Qt CRITICAL: {message}")
        elif msg_type == QtMsgType.QtFatalMsg:
            log.critical(f"Qt FATAL: {message}")
    qInstallMessageHandler(_qt_msg_handler)

    # 5. Системная информация при старте
    log.info(
        f"Система: {platform.system()} {platform.release()} | "
        f"Python {platform.python_version()} | "
        f"PID={os.getpid()} PPID={os.getppid()}"
    )
    log.info(
        f"Пользователь: {os.environ.get('USERNAME','?')} | "
        f"Компьютер: {os.environ.get('COMPUTERNAME','?')} | "
        f"AppData: {APPDATA_DIR}"
    )


# ─── Одиночный экземпляр (Windows: msvcrt вместо fcntl) ───
import msvcrt

def acquire_lock() -> bool:
    try:
        lf = open(LOCK_FILE, "w")
        msvcrt.locking(lf.fileno(), msvcrt.LK_NBLCK, 1)
        lf.write(str(os.getpid()))
        lf.flush()
        acquire_lock._fh = lf
        return True
    except (IOError, OSError):
        return False


# ─── Определить тип окна ───
def resolve_type(level: str, msg_type: str) -> str:
    """
    Если type явно передан — использовать его.
    Иначе определить по level:
      critical/warning → fullscreen
      info             → popup
    """
    if msg_type in ("fullscreen", "popup"):
        return msg_type
    if level in ("critical", "warning"):
        return "fullscreen"
    return "popup"


# ─── Markdown → HTML ───
def md_to_html(text: str, accent: str) -> str:
    t = text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    t = re.sub(r'^### (.+)$', r'<h3>\1</h3>', t, flags=re.MULTILINE)
    t = re.sub(r'^## (.+)$',  r'<h2>\1</h2>', t, flags=re.MULTILINE)
    t = re.sub(r'^# (.+)$',   r'<h1>\1</h1>', t, flags=re.MULTILINE)
    t = re.sub(r'^&gt; (.+)$',r'<blockquote>\1</blockquote>', t, flags=re.MULTILINE)
    t = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', t)
    t = re.sub(r'\*(.+?)\*',     r'<i>\1</i>', t)
    t = re.sub(r'`(.+?)`',       r'<code>\1</code>', t)
    t = re.sub(r'\n{2,}', '</p><p>', t)
    t = t.replace('\n', '<br/>')
    return (
        "<html><head><style>"
        "body{font-family:'DejaVu Sans',Arial,sans-serif;font-size:14px;"
        f"color:#f0f0f0;background:transparent;margin:0;padding:0}}"
        f"h1{{font-size:18px;color:{accent};margin:6px 0 4px}}"
        f"h2{{font-size:15px;color:{accent};margin:5px 0 3px}}"
        "h3{font-size:13px;color:#ccc;margin:4px 0 2px}"
        "b,strong{color:#fff} i,em{color:#ddd}"
        f"blockquote{{border-left:3px solid {accent};padding-left:10px;"
        "color:#aaa;margin:6px 0;font-style:italic}"
        "code{background:rgba(255,255,255,.12);padding:1px 5px;"
        "border-radius:3px;font-family:monospace;font-size:12px}"
        "p{margin:5px 0;line-height:1.6}"
        f"</style></head><body><p>{t}</p></body></html>"
    )


# ─── Иконка трея ───
def make_icon(color: str = "#4f8fff") -> QIcon:
    px = QPixmap(22, 22)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawEllipse(1, 1, 20, 20)
    p.setPen(QColor("#ffffff"))
    p.setFont(QFont("Arial", 13, QFont.Bold))
    p.drawText(0, 0, 22, 22, Qt.AlignCenter, "!")
    p.end()
    return QIcon(px)


# ─── Сигнал HTTP → Qt ───
class Notifier(QObject):
    # title, body, level, type, timeout
    show_message = pyqtSignal(str, str, str, str, int)


# ─── HTTP сервер ───
class Handler(BaseHTTPRequestHandler):

    def do_POST(self):
        if self.headers.get("X-Token", "") != TOKEN:
            log.warning(f"Неверный токен от {self.client_address[0]}")
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"Forbidden")
            return

        n   = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        try:
            d        = json.loads(raw)
            title    = d.get("title",   "Уведомление")
            message  = d.get("message", "")
            level    = d.get("level",   "info")
            msg_type = d.get("type",    "")
            timeout  = int(d.get("timeout", POPUP_DEFAULT_TIMEOUT))
        except Exception as e:
            log.error(f"JSON parse error: {e}")
            title    = "Уведомление"
            message  = raw.decode("utf-8", errors="replace")
            level    = "info"
            msg_type = ""
            timeout  = POPUP_DEFAULT_TIMEOUT

        win_type = resolve_type(level, msg_type)
        log.info(f"Получено [{level}] type={win_type}: {title}")

        if _notifier is not None:
            _notifier.show_message.emit(title, message, level, win_type, timeout)
        else:
            log.error("Notifier не инициализирован!")

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *a):
        pass


def _check_port() -> bool:
    """Проверить что порт 9988 слушается."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            return s.connect_ex(('127.0.0.1', PORT)) == 0
    except Exception:
        return False


def run_server():
    try:
        srv = HTTPServer(("0.0.0.0", PORT), Handler)
        log.info(f"HTTP сервер слушает 0.0.0.0:{PORT}")
        srv.serve_forever()
    except OSError as e:
        log.critical(f"Не удалось запустить сервер на порту {PORT}: {e}")
        sys.exit(1)


# ══════════════════════════════════════════════════
# ─── Полноэкранное окно (fullscreen) ───
# ══════════════════════════════════════════════════
class NotifyFullscreen(QDialog):

    def __init__(self, title: str, message: str, level: str):
        super().__init__(None)

        cfg    = LEVELS.get(level, LEVELS["info"])
        accent = cfg["accent"]
        bg     = cfg["bg"]

        self.setWindowFlags(
            Qt.Dialog                     |
            Qt.FramelessWindowHint        |
            Qt.WindowStaysOnTopHint
        )

        desk = QDesktopWidget()
        rect = desk.screenGeometry(0)
        for i in range(1, desk.screenCount()):
            rect = rect.united(desk.screenGeometry(i))
        self.setGeometry(rect)

        # Контент прямо на фон — без карточки и рамки
        from PyQt5.QtGui import QPalette, QColor as QC

        # Фон диалога через QPalette — не stylesheet
        pal = self.palette()
        pal.setColor(QPalette.Window,     QC(bg))
        pal.setColor(QPalette.WindowText, QC("#ffffff"))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

        center = QWidget(self)
        center.setMinimumWidth(rect.width() - 120)
        cp = center.palette()
        cp.setColor(QPalette.Window, QC(bg))
        center.setPalette(cp)
        center.setAutoFillBackground(True)

        lay = QVBoxLayout(center)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(18)

        # Badge уровня — через QPalette + шрифт
        lv = QLabel(cfg["label"].upper())
        lv.setAlignment(Qt.AlignCenter)
        lv.setFixedHeight(28)
        lv_pal = lv.palette()
        lv_pal.setColor(QPalette.Window,     QC(accent))
        lv_pal.setColor(QPalette.WindowText, QC("#0f0f0f"))
        lv.setPalette(lv_pal)
        lv.setAutoFillBackground(True)
        lv_font = QFont(); lv_font.setPointSize(9); lv_font.setBold(True)
        lv.setFont(lv_font)
        lv.setContentsMargins(20, 5, 20, 5)

        # Заголовок
        ti = QLabel(title)
        ti.setWordWrap(True)
        ti.setAlignment(Qt.AlignCenter)
        ti_pal = ti.palette()
        ti_pal.setColor(QPalette.Window,     QC(bg))
        ti_pal.setColor(QPalette.WindowText, QC("#ffffff"))
        ti.setPalette(ti_pal)
        ti.setAutoFillBackground(True)
        f = QFont(); f.setPointSize(22); f.setBold(True)
        ti.setFont(f)

        # Разделитель
        ln = QLabel()
        ln.setFixedHeight(2)
        ln_pal = ln.palette()
        ln_pal.setColor(QPalette.Window, QC(accent))
        ln.setPalette(ln_pal)
        ln.setAutoFillBackground(True)

        # Тело сообщения — QTextBrowser стиль через Qt-совместимый CSS
        body = QTextBrowser()
        body.setOpenExternalLinks(False)
        body.setHtml(md_to_html(message, accent))
        body.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body_pal = body.palette()
        body_pal.setColor(QPalette.Base,        QC(bg))
        body_pal.setColor(QPalette.Text,        QC("#f0f0f0"))
        body_pal.setColor(QPalette.Window,      QC(bg))
        body_pal.setColor(QPalette.WindowText,  QC("#f0f0f0"))
        body.setPalette(body_pal)
        # Только border через stylesheet — это Qt5 точно поддерживает
        body.setStyleSheet("QTextBrowser { border: none; }")

        # Кнопка — только border-radius и цвета, без hover (проблемный)
        btn = QPushButton("✓  Принял к сведению")
        btn.setFixedHeight(52)
        btn.setCursor(Qt.PointingHandCursor)
        # Кнопка — stylesheet работает на QPushButton надёжно
        # Цвет фона и текста задаём здесь, не через QPalette
        btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {accent};"
            f"  color: #0f0f0f;"
            f"  border: none;"
            f"  border-radius: 12px;"
            f"  font-size: 16px;"
            f"  font-weight: bold;"
            f"  padding: 8px;"
            f"}}"
        )
        btn.clicked.connect(self.accept)

        lay.addWidget(lv, alignment=Qt.AlignCenter)
        lay.addWidget(ti)
        lay.addWidget(ln)
        lay.addWidget(body, stretch=1)   # body занимает всё свободное место
        lay.addSpacing(8)
        lay.addWidget(btn)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(60, 50, 60, 50)
        outer.addWidget(center)  # растягиваем на всю ширину

        QTimer.singleShot(150, self._grab)

    def _grab(self):
        self.raise_()
        self.activateWindow()
        self.grabKeyboard()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.accept()

    def closeEvent(self, e):
        self.releaseKeyboard()
        super().closeEvent(e)


# ══════════════════════════════════════════════════
# ─── Попап окно (Windows 11 стиль) ───
# ══════════════════════════════════════════════════
POPUP_WIDTH  = 380
POPUP_MARGIN = 16
POPUP_GAP    = 10

_popups = []


def _popup_position(height):
    """Вернуть (x, y) для нового попапа снизу-справа."""
    desk   = QDesktopWidget()
    screen = desk.availableGeometry(desk.primaryScreen())
    x = screen.x() + screen.width()  - POPUP_WIDTH - POPUP_MARGIN
    y = screen.y() + screen.height() - POPUP_MARGIN
    for p in reversed(_popups):
        h = p.height() or 120
        y -= h + POPUP_GAP
    y -= height
    return x, y


class NotifyPopup(QWidget):

    def __init__(self, title: str, message: str, level: str, timeout: int):
        super().__init__(None)

        self._timeout = timeout
        self._elapsed = 0
        cfg    = LEVELS.get(level, LEVELS["info"])
        accent = cfg["accent"]
        BG     = "#1e2235"

        # Qt.ToolTip — WM не центрирует и не перемещает tooltip окна
        self.setWindowFlags(
            Qt.ToolTip             |
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        # Фон через palette — надёжнее чем stylesheet на ALT Linux
        from PyQt5.QtGui import QPalette, QColor as QC
        pal = self.palette()
        pal.setColor(QPalette.Window, QC(BG))
        self.setPalette(pal)
        self.setAutoFillBackground(True)
        self.setFixedWidth(POPUP_WIDTH)

        # Layout
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Цветная полоска
        top_bar = QLabel()
        top_bar.setFixedHeight(4)
        tp = top_bar.palette()
        tp.setColor(QPalette.Window, QC(accent))
        top_bar.setPalette(tp)
        top_bar.setAutoFillBackground(True)
        root.addWidget(top_bar)

        inner = QVBoxLayout()
        inner.setContentsMargins(14, 12, 14, 12)
        inner.setSpacing(6)

        # Шапка
        hbox = QHBoxLayout()
        lv = QLabel(cfg["label"])
        lv.setStyleSheet(
            f"color:{accent};font-size:11px;font-weight:800;"
            f"background-color:{BG};"
        )
        hbox.addWidget(lv)
        hbox.addStretch()
        btn = QPushButton("✕")
        btn.setFixedSize(20, 20)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton{{background-color:{BG};color:#8891aa;border:none;font-size:13px;}}"
            f"QPushButton:hover{{color:#ffffff;background-color:{BG};}}"
        )
        btn.clicked.connect(self._close)
        hbox.addWidget(btn)
        inner.addLayout(hbox)

        # Заголовок
        t = QLabel(title)
        t.setWordWrap(True)
        f = QFont(); f.setPointSize(13); f.setBold(True)
        t.setFont(f)
        t.setStyleSheet(f"color:#ffffff;background-color:{BG};")
        inner.addWidget(t)

        # Текст
        if message.strip():
            import re
            plain = re.sub(r'#{1,3}\s', '', message)
            plain = re.sub(r'\*\*(.+?)\*\*', r'\1', plain)
            plain = re.sub(r'\*(.+?)\*', r'\1', plain)
            plain = re.sub(r'^> ?', '', plain, flags=re.MULTILINE)
            plain = plain.strip()[:150]
            m = QLabel(plain)
            m.setWordWrap(True)
            m.setStyleSheet(f"color:#d0d8f0;font-size:13px;background-color:{BG};")
            inner.addWidget(m)

        # Прогресс-бар
        if timeout > 0:
            self._pb = QProgressBar()
            self._pb.setRange(0, timeout * 10)
            self._pb.setValue(timeout * 10)
            self._pb.setFixedHeight(4)
            self._pb.setTextVisible(False)
            self._pb.setStyleSheet(
                f"QProgressBar{{background-color:#2a3050;border:none;}}"
                f"QProgressBar::chunk{{background-color:{accent};}}"
            )
            inner.addWidget(self._pb)
        else:
            self._pb = None

        root.addLayout(inner)

        # Вычислить размер и сразу поставить в правый нижний угол
        self.adjustSize()
        x, y = _popup_position(self.height() or 120)
        self.setGeometry(x, y, POPUP_WIDTH, self.height() or 120)

        _popups.append(self)
        self.show()

        # Windows позиционирует окна точно — повторный вызов не требуется,
        # но один контрольный пересчёт после отрисовки не помешает
        def _reposition():
            desk   = QDesktopWidget()
            screen = desk.availableGeometry(desk.primaryScreen())
            ox, oy = screen.x(), screen.y()
            sw, sh = screen.width(), screen.height()
            y = oy + sh - POPUP_MARGIN
            for p in reversed(_popups):
                if not p.isVisible(): continue
                ph = p.height() or 120
                y -= ph
                p.move(ox + sw - POPUP_WIDTH - POPUP_MARGIN, y)
                y -= POPUP_GAP

        QTimer.singleShot(50, _reposition)
        if timeout > 0:
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._tick)
            self._timer.start(100)

    def _tick(self):
        self._elapsed += 1
        rem = self._timeout * 10 - self._elapsed
        if self._pb:
            self._pb.setValue(max(0, rem))
        if rem <= 0:
            self._close()

    def _close(self):
        if hasattr(self, '_timer'):
            self._timer.stop()
        if self in _popups:
            _popups.remove(self)
        self.close()

    def closeEvent(self, e):
        if self in _popups:
            _popups.remove(self)
        super().closeEvent(e)


# ══════════════════════════════════════════════════
# ─── Трей ───
# ══════════════════════════════════════════════════
class TrayApp(QSystemTrayIcon):

    def __init__(self, app: QApplication):
        super().__init__(make_icon("#4f8fff"), app)
        self.app         = app
        self._count      = 0
        self._last_time  = "—"
        self._last_title = "—"
        self._start_time = datetime.now().strftime("%H:%M:%S")
        self._status_act = None
        self._last_act   = None
        self._attempt    = 0

        self.setToolTip(f"Notify Agent v{VERSION} | порт {PORT}")
        self._try_show()

    def _try_show(self):
        self._attempt += 1
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._build_menu()
            self.show()
            log.info(f"Трей инициализирован (попытка {self._attempt})")
        elif self._attempt <= 30:
            log.debug(f"Трей недоступен, повтор через 2 сек (попытка {self._attempt})")
            QTimer.singleShot(2000, self._try_show)
        else:
            log.warning("Трей недоступен — работаем без иконки")

    def _build_menu(self):
        menu = QMenu()

        hdr = QAction(f"📢 Notify Agent v{VERSION}", menu)
        hdr.setEnabled(False)
        f = hdr.font(); f.setBold(True); hdr.setFont(f)
        menu.addAction(hdr)
        menu.addSeparator()

        self._status_act = QAction("✅ Активен | получено: 0", menu)
        self._status_act.setEnabled(False)
        menu.addAction(self._status_act)

        self._last_act = QAction("📭 Сообщений ещё не было", menu)
        self._last_act.setEnabled(False)
        menu.addAction(self._last_act)

        menu.addSeparator()

        for text in [f"🔌 Порт: {PORT}",
                     f"🕐 Запущен: {self._start_time}",
                     f"📄 Лог: {LOG_FILE}"]:
            a = QAction(text, menu); a.setEnabled(False); menu.addAction(a)

        menu.addSeparator()

        # Тест fullscreen
        tf = QAction("🖥  Тест — полный экран", menu)
        tf.triggered.connect(lambda: self.on_message(
            "🔔 Тест полного экрана",
            f"## Агент работает!\n\nВерсия **v{VERSION}**\n\n> Порт **{PORT}** активен.",
            "warning", "fullscreen", 0
        ))
        menu.addAction(tf)

        # Тест popup
        tp = QAction("💬  Тест — попап", menu)
        tp.triggered.connect(lambda: self.on_message(
            "🔔 Тест попапа",
            "Это маленькое уведомление снизу справа.",
            "info", "popup", 8
        ))
        menu.addAction(tp)

        menu.addSeparator()

        quit_act = QAction("✕ Выход", menu)
        def _quit_with_log():
            log.info("Пользователь нажал Выход в меню трея")
            self.app.quit()
        quit_act.triggered.connect(_quit_with_log)
        menu.addAction(quit_act)

        self.setContextMenu(menu)

    def _update_menu(self):
        if self._status_act:
            self._status_act.setText(f"✅ Активен | получено: {self._count}")
        if self._last_act:
            t = self._last_title[:35] + "…" if len(self._last_title) > 35 else self._last_title
            self._last_act.setText(f"📩 {self._last_time}  {t}")

    @pyqtSlot(str, str, str, str, int)
    def on_message(self, title: str, message: str, level: str,
                   win_type: str, timeout: int):
        self._count     += 1
        self._last_time  = datetime.now().strftime("%H:%M:%S")
        self._last_title = title
        self._update_menu()

        colors = {"info": "#4f8fff", "warning": "#ffb347", "critical": "#ff4444"}
        self.setIcon(make_icon(colors.get(level, "#4f8fff")))

        log.info(f"Показываем [{win_type}] [{level}]: {title}")

        try:
            if win_type == "popup":
                # Попап — не блокирует, создаётся и живёт сам
                NotifyPopup(title, message, level, timeout)
            else:
                # Fullscreen — блокирует до нажатия ОК
                dlg = NotifyFullscreen(title, message, level)
                dlg.exec_()
                log.info("Fullscreen закрыт")
        except Exception as e:
            log.error(f"Ошибка окна: {e}", exc_info=True)

        QTimer.singleShot(2000, lambda: self.setIcon(make_icon("#4f8fff")))


# ─── main ───
def main():
    global _notifier, _tray_app

    # Максимальное логирование — перехват всех исключений и сигналов
    _install_hooks()

    log.info(f"=== Notify Agent v{VERSION} старт ==="
             f" PID={os.getpid()} USER={os.environ.get('USERNAME','?')}"
             f" PC={os.environ.get('COMPUTERNAME','?')}")

    if not acquire_lock():
        log.info("Агент уже запущен (lock занят). Штатный выход.")
        sys.exit(0)

    app = QApplication(sys.argv)
    app.setApplicationName("NotifyAgent")
    app.setQuitOnLastWindowClosed(False)

    _notifier = Notifier()
    _tray_app = TrayApp(app)
    _notifier.show_message.connect(_tray_app.on_message)

    t = threading.Thread(target=run_server, daemon=True)
    t.start()

    # Логировать ПРИЧИНУ выхода из event loop
    def _on_about_to_quit():
        import traceback
        log.warning(
            "app.quit() вызван — агент завершается\n"
            "Стек вызова:\n" +
            "".join(traceback.format_stack())
        )
    app.aboutToQuit.connect(_on_about_to_quit)

    # Heartbeat — каждые 5 минут пишем что агент жив
    # Если записей нет — значит упал без лога
    def _heartbeat():
        log.debug(
            f"[HEARTBEAT] жив | порт={'открыт' if _check_port() else 'ЗАКРЫТ'} | "
            f"попапов={len(_popups)} | трей={'есть' if _tray_app and _tray_app.isVisible() else 'нет'}"
        )
    _hb_timer = QTimer()
    _hb_timer.timeout.connect(_heartbeat)
    _hb_timer.start(5 * 60 * 1000)   # каждые 5 минут

    log.info("Qt event loop запущен")
    code = app.exec_()
    log.info(f"Агент завершён штатно, код={code}")
    sys.exit(0)


if __name__ == "__main__":
    main()
