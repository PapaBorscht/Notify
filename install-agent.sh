#!/bin/bash
# ================================================================
# Установка агента уведомлений — ALT Linux
# Запуск: sudo bash install-agent.sh
# ================================================================
set -e

INSTALL_DIR="/opt/notify-agent"
XDG_DIR="/etc/xdg/autostart"
SYSTEMD_USER_DIR="/etc/systemd/user"
PROFILED="/etc/profile.d"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="/var/log/notify-agent-install.log"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC}  $1" | tee -a "$LOG"; }
warn() { echo -e "${YELLOW}[!!]${NC}  $1" | tee -a "$LOG"; }
info() { echo -e "${BLUE}[..]${NC}  $1" | tee -a "$LOG"; }
fail() { echo -e "${RED}[ERR]${NC} $1" | tee -a "$LOG"; exit 1; }

echo "================================================================" | tee "$LOG"
echo "  Установка агента уведомлений  $(date)"                          | tee -a "$LOG"
echo "================================================================" | tee -a "$LOG"

[ "$EUID" -eq 0 ] || fail "Запустите от root: sudo bash $0"
command -v python3 >/dev/null 2>&1 || fail "python3 не найден"
ok "python3: $(python3 --version 2>&1)"

# PyQt5
if ! python3 -c "from PyQt5.QtWidgets import QApplication" 2>/dev/null; then
    warn "Устанавливаем PyQt5..."
    apt-get install -y python3-module-pyqt5 2>&1 | tee -a "$LOG" \
        || fail "Не удалось установить PyQt5"
fi
ok "PyQt5 OK"

# ── 1. Файлы ──
echo ""
info "[1/6] Копирование файлов в $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_DIR/agent.py"       "$INSTALL_DIR/agent.py"
cp "$SCRIPT_DIR/sender.py"      "$INSTALL_DIR/sender.py"
cp "$SCRIPT_DIR/agent-start.sh" "$INSTALL_DIR/agent-start.sh"
chmod 644 "$INSTALL_DIR/agent.py"
chmod 755 "$INSTALL_DIR/sender.py"
chmod 755 "$INSTALL_DIR/agent-start.sh"
chmod 755 "$INSTALL_DIR"
chown -R root:root "$INSTALL_DIR"
ok "Файлы скопированы"

# ── 2. Переменные окружения ──
echo ""
info "[2/6] Настройка /etc/profile.d/notify-agent.sh..."
cat > "$PROFILED/notify-agent.sh" << 'ENVEOF'
export QT_QPA_PLATFORMTHEME=""
export QT_STYLE_OVERRIDE=""
ENVEOF
chmod 644 "$PROFILED/notify-agent.sh"
ok "QT_QPA_PLATFORMTHEME=\"\" применён для всех пользователей"

# ── 3. Systemd user service ──
echo ""
info "[3/6] Установка systemd user service..."
mkdir -p "$SYSTEMD_USER_DIR"
cp "$SCRIPT_DIR/notify-agent.service" "$SYSTEMD_USER_DIR/notify-agent.service"
chmod 644 "$SYSTEMD_USER_DIR/notify-agent.service"
systemctl --global enable notify-agent 2>/dev/null && \
    ok "Сервис включён глобально" || warn "systemctl --global не сработал"

# ── 4. XDG autostart (резерв) ──
echo ""
info "[4/6] Настройка /etc/xdg/autostart/..."
mkdir -p "$XDG_DIR"
cat > "$XDG_DIR/notify-agent.desktop" << DESKEOF
[Desktop Entry]
Type=Application
Name=Notify Agent
Comment=Агент системы экстренного оповещения
Exec=$INSTALL_DIR/agent-start.sh
Icon=dialog-warning
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-MATE-Autostart-enabled=true
X-KDE-autostart-after=panel
DESKEOF
chmod 644 "$XDG_DIR/notify-agent.desktop"
ok "XDG autostart настроен"

# ── 5. Симлинки ──
echo ""
info "[5/6] Создание симлинков..."
ln -sf "$INSTALL_DIR/sender.py"      /usr/local/bin/notify-send-test
ln -sf "$INSTALL_DIR/agent-start.sh" /usr/local/bin/notify-agent
chmod 755 /usr/local/bin/notify-send-test
chmod 755 /usr/local/bin/notify-agent
ok "Симлинки созданы"

# ── 6. Запуск в активных X11-сессиях ──
echo ""
info "[6/6] Запуск агента в активных сессиях..."
STARTED=0

while read -r SID _seat USERNAME _rest; do
    [ -z "$USERNAME" ] || [ "$USERNAME" = "root" ] && continue

    TYPE=$(loginctl show-session "$SID" -p Type --value 2>/dev/null || echo "")
    [ "$TYPE" = "x11" ] || [ "$TYPE" = "wayland" ] || continue

    UID_NUM=$(id -u "$USERNAME" 2>/dev/null) || continue
    UHOME=$(getent passwd "$USERNAME" | cut -d: -f6)
    DISP=$(loginctl show-session "$SID" -p Display --value 2>/dev/null || echo ":0")
    [ -z "$DISP" ] && DISP=":0"
    LOGF="/tmp/notify-agent-${USERNAME}.log"

    # ── ГЛАВНАЯ ПРОВЕРКА: порт 9988 уже занят? ──
    if ss -tlnp 2>/dev/null | grep -q ":9988"; then
        ok "Агент уже работает на порту 9988 для $USERNAME — пропускаем запуск"
        STARTED=$((STARTED+1))
        continue
    fi

    # Остановить старые процессы если есть (без порта)
    pkill -u "$USERNAME" -f "agent-start.sh" 2>/dev/null || true
    pkill -u "$USERNAME" -f "agent.py"       2>/dev/null || true
    rm -f /tmp/notify-agent.lock
    sleep 1

    # Попытка через systemd --user
    if sudo -u "$USERNAME" \
        XDG_RUNTIME_DIR="/run/user/${UID_NUM}" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${UID_NUM}/bus" \
        systemctl --user start notify-agent 2>/dev/null; then
        sleep 2
        if ss -tlnp 2>/dev/null | grep -q ":9988"; then
            ok "Запущен через systemd --user для: $USERNAME"
            STARTED=$((STARTED+1))
            continue
        fi
    fi

    # Fallback: запуск напрямую
    warn "systemd --user не сработал для $USERNAME, запускаем напрямую..."
    sudo -u "$USERNAME" touch "$LOGF"
    sudo -u "$USERNAME" \
        DISPLAY="$DISP" \
        XAUTHORITY="${UHOME}/.Xauthority" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${UID_NUM}/bus" \
        XDG_RUNTIME_DIR="/run/user/${UID_NUM}" \
        HOME="$UHOME" \
        QT_QPA_PLATFORMTHEME="" \
        QT_STYLE_OVERRIDE="" \
        nohup bash "$INSTALL_DIR/agent-start.sh" &

    sleep 3

    # Проверка по порту — не по процессу
    if ss -tlnp 2>/dev/null | grep -q ":9988"; then
        ok "Агент запущен, порт 9988 слушается ✓ (пользователь: $USERNAME)"
        STARTED=$((STARTED+1))
    else
        warn "Порт 9988 не открылся для $USERNAME"
        warn "Лог: tail -f $LOGF"
        tail -5 "$LOGF" 2>/dev/null | while IFS= read -r L; do echo "        $L"; done
    fi

done < <(loginctl list-sessions --no-legend 2>/dev/null)

[ "$STARTED" -eq 0 ] && warn "Нет активных X11-сессий — агент стартует при логине"

echo ""
echo "================================================================"
echo -e "  ${GREEN}Установка завершена!${NC}"
echo "================================================================"
echo ""
echo "  Управление:"
echo "    systemctl --user status  notify-agent"
echo "    systemctl --user restart notify-agent"
echo "    journalctl --user -u notify-agent -f"
echo "    tail -f /tmp/notify-agent-<пользователь>.log"
echo ""
echo "  Тест:"
echo "    curl -X POST http://127.0.0.1:9988 \\"
echo "      -H 'X-Token: supersecrettoken123' \\"
echo "      -H 'Content-Type: application/json' \\"
echo "      -d '{\"title\":\"Тест\",\"message\":\"## Работает!\",\"level\":\"info\"}'"
echo ""
echo "  Лог установки: $LOG"
echo ""
