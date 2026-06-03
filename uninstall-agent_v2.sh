#!/bin/bash
# ================================================================
# Полное удаление notify-agent
# Запуск: sudo bash uninstall-agent.sh
# ================================================================
set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC}  $1"; }
warn() { echo -e "${YELLOW}[!!]${NC}  $1"; }
info() { echo -e "${BLUE}[..]${NC}  $1"; }

echo "================================================================"
echo "  Удаление notify-agent  $(date)"
echo "================================================================"
echo ""

[ "$EUID" -eq 0 ] || { echo "Запустите от root: sudo bash $0"; exit 1; }

# ── 1. Остановить процессы ──────────────────────────────────────
info "[1/7] Остановка процессов..."

# Остановить systemd user service у всех пользователей
while IFS= read -r USERNAME; do
    [ -z "$USERNAME" ] && continue
    UID_NUM=$(id -u "$USERNAME" 2>/dev/null) || continue
    sudo -u "$USERNAME" \
        XDG_RUNTIME_DIR="/run/user/${UID_NUM}" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${UID_NUM}/bus" \
        systemctl --user stop notify-agent 2>/dev/null && \
        warn "Остановлен systemd сервис для: $USERNAME" || true
done < <(loginctl list-sessions --no-legend 2>/dev/null | awk '{print $3}' | sort -u)

# Убить все процессы
pkill -f "agent-start.sh" 2>/dev/null && warn "agent-start.sh убит" || true
pkill -f "agent.py"       2>/dev/null && warn "agent.py убит"       || true
sleep 1
# Если не убились — SIGKILL
pkill -9 -f "agent-start.sh" 2>/dev/null || true
pkill -9 -f "agent.py"       2>/dev/null || true
ok "Процессы остановлены"

# ── 2. Systemd сервис ───────────────────────────────────────────
info "[2/7] Удаление systemd user service..."
systemctl --global disable notify-agent 2>/dev/null && \
    warn "Сервис отключён глобально" || true

for f in \
    /etc/systemd/user/notify-agent.service \
    /usr/lib/systemd/user/notify-agent.service \
    /etc/xdg/systemd/user/notify-agent.service; do
    [ -f "$f" ] && rm -f "$f" && warn "Удалён: $f" || true
done

systemctl daemon-reload 2>/dev/null || true
ok "Systemd сервис удалён"

# ── 3. XDG autostart ───────────────────────────────────────────
info "[3/7] Удаление XDG autostart..."
for f in \
    /etc/xdg/autostart/notify-agent.desktop \
    /etc/skel/.config/autostart/notify-agent.desktop; do
    [ -f "$f" ] && rm -f "$f" && warn "Удалён: $f" || true
done

# Удалить из домашних папок всех пользователей
for HOME_DIR in /home/*; do
    f="$HOME_DIR/.config/autostart/notify-agent.desktop"
    [ -f "$f" ] && rm -f "$f" && warn "Удалён: $f" || true
done
ok "XDG autostart удалён"

# ── 4. Переменные окружения ─────────────────────────────────────
info "[4/7] Удаление /etc/profile.d/notify-agent.sh..."
[ -f /etc/profile.d/notify-agent.sh ] && \
    rm -f /etc/profile.d/notify-agent.sh && \
    warn "Удалён: /etc/profile.d/notify-agent.sh" || true
ok "Переменные окружения удалены"

# ── 5. Файлы агента ─────────────────────────────────────────────
info "[5/7] Удаление файлов агента..."
[ -d /opt/notify-agent ] && \
    rm -rf /opt/notify-agent && \
    warn "Удалена директория: /opt/notify-agent" || true
ok "Файлы агента удалены"

# ── 6. Симлинки ─────────────────────────────────────────────────
info "[6/7] Удаление симлинков..."
for f in \
    /usr/local/bin/notify-agent \
    /usr/local/bin/notify-send-test; do
    [ -L "$f" ] || [ -f "$f" ] && rm -f "$f" && warn "Удалён: $f" || true
done
ok "Симлинки удалены"

# ── 7. Временные файлы ──────────────────────────────────────────
info "[7/7] Очистка временных файлов..."
rm -f /tmp/notify-agent.lock
rm -f /tmp/notify-agent-start.lock
rm -f /tmp/notify-agent*.log
rm -f /tmp/nohup.out
ok "Временные файлы удалены"

# ── Итог ────────────────────────────────────────────────────────
echo ""
echo "================================================================"
echo -e "  ${GREEN}notify-agent полностью удалён${NC}"
echo "================================================================"
echo ""
echo "  Осталось (намеренно не удаляется):"
echo "    /var/log/notify-agent-install.log  ← лог установки"
echo "    /var/log/ansible-install-notify-agent.done  ← маркер Ansible"
echo ""

# Предложить удалить маркер Ansible
if [ -f /var/log/ansible-install-notify-agent.done ]; then
    echo -e "  ${YELLOW}Маркер Ansible найден.${NC} Удалить? [y/N]"
    read -r -t 10 ANSWER || ANSWER="n"
    if [[ "$ANSWER" =~ ^[Yy]$ ]]; then
        rm -f /var/log/ansible-install-notify-agent.done
        ok "Маркер Ansible удалён — при следующем запуске плейбука агент установится заново"
    fi
fi
