#!/bin/bash
# ================================================================
# Установка панели администратора уведомлений
# Запуск: sudo bash install-panel.sh
# ================================================================
set -e

INSTALL_DIR="/opt/notify-panel"
SERVICE_FILE="/etc/systemd/system/notify-panel.service"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="/var/log/notify-panel-install.log"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC}  $1" | tee -a "$LOG_FILE"; }
warn() { echo -e "${YELLOW}[!!]${NC}  $1" | tee -a "$LOG_FILE"; }
info() { echo -e "${BLUE}[..]${NC}  $1" | tee -a "$LOG_FILE"; }
fail() { echo -e "${RED}[ERR]${NC} $1" | tee -a "$LOG_FILE"; exit 1; }

echo "================================================================" | tee "$LOG_FILE"
echo "  Установка панели администратора  $(date)"                       | tee -a "$LOG_FILE"
echo "================================================================" | tee -a "$LOG_FILE"

[ "$EUID" -eq 0 ] || fail "Запустите от root: sudo bash $0"
command -v python3 >/dev/null 2>&1 || fail "python3 не найден"
ok "python3: $(python3 --version)"

# Определить пользователя (не root) для запуска сервиса
RUN_AS="${SUDO_USER:-}"
if [ -z "$RUN_AS" ] || [ "$RUN_AS" = "root" ]; then
    # Взять первого не-root пользователя с home
    RUN_AS=$(getent passwd | awk -F: '$3>=1000 && $3<65534 && $6~/\/home/ {print $1; exit}')
fi
[ -z "$RUN_AS" ] && RUN_AS="root"
info "Сервис будет запущен от пользователя: $RUN_AS"

# ── 1. Скопировать файлы ──
echo ""
info "[1/5] Копирование файлов..."
mkdir -p "$INSTALL_DIR/data"

cp "$SCRIPT_DIR/server.py"  "$INSTALL_DIR/server.py"
cp "$SCRIPT_DIR/index.html" "$INSTALL_DIR/index.html"
cp "$SCRIPT_DIR/sender.py"  "$INSTALL_DIR/sender.py"

# Права:
# server.py, sender.py — исполнять только владельцу и группе
# index.html — читать всем (отдаётся браузеру)
# data/ — читать и писать только владельцу
chmod 750 "$INSTALL_DIR/server.py"
chmod 750 "$INSTALL_DIR/sender.py"
chmod 644 "$INSTALL_DIR/index.html"
chmod 750 "$INSTALL_DIR"
chmod 700 "$INSTALL_DIR/data"

# Создать пустые JSON если не существуют
for f in hosts.json templates.json history.json; do
    if [ ! -f "$INSTALL_DIR/data/$f" ]; then
        echo "[]" > "$INSTALL_DIR/data/$f"
        ok "Создан $INSTALL_DIR/data/$f"
    else
        info "$INSTALL_DIR/data/$f уже существует — не перезаписываем"
    fi
done
chmod 600 "$INSTALL_DIR/data/"*.json

# Назначить владельца
chown -R "$RUN_AS":"$RUN_AS" "$INSTALL_DIR"
ok "Владелец файлов: $RUN_AS"
ok "Права: server.py=750, index.html=644, data/=700, *.json=600"

# ── 2. Симлинк ──
echo ""
info "[2/5] Создание симлинка..."
ln -sf "$INSTALL_DIR/server.py" /usr/local/bin/notify-panel
chmod 755 /usr/local/bin/notify-panel
ok "Симлинк: /usr/local/bin/notify-panel"

# ── 3. Systemd сервис ──
echo ""
info "[3/5] Создание systemd сервиса..."

cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Notify Admin Panel — система оповещения
After=network.target
Wants=network.target

[Service]
Type=simple
User=$RUN_AS
Group=$RUN_AS
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 $INSTALL_DIR/server.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
# Переменные окружения
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

chmod 644 "$SERVICE_FILE"
ok "Сервис создан: $SERVICE_FILE"

# ── 4. Запустить сервис ──
echo ""
info "[4/5] Запуск сервиса..."
systemctl daemon-reload
systemctl enable notify-panel
systemctl restart notify-panel
sleep 2

if systemctl is-active --quiet notify-panel; then
    ok "Сервис notify-panel запущен и добавлен в автозапуск"
else
    warn "Сервис не запустился!"
    warn "Диагностика: journalctl -u notify-panel -n 20"
    journalctl -u notify-panel -n 10 --no-pager 2>/dev/null | while read -r L; do warn "  $L"; done
fi

# ── 5. Проверка порта ──
echo ""
info "[5/5] Проверка..."
sleep 1
if ss -tlnp 2>/dev/null | grep -q ":8080"; then
    ok "Порт 8080 слушается"
else
    warn "Порт 8080 не обнаружен — проверьте: journalctl -u notify-panel -f"
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')

echo ""
echo "================================================================"
echo -e "  ${GREEN}Установка завершена!${NC}"
echo "================================================================"
echo ""
echo "  Открыть панель:"
echo "    http://localhost:8080"
[ -n "$IP" ] && echo "    http://$IP:8080  (из сети)"
echo ""
echo "  Логин:  admin"
echo -e "  Пароль: admin123  ${YELLOW}← поменяй в $INSTALL_DIR/server.py${NC}"
echo ""
echo "  Управление сервисом:"
echo "    systemctl status  notify-panel"
echo "    systemctl restart notify-panel"
echo "    journalctl -u notify-panel -f"
echo ""
echo "  Лог установки: $LOG_FILE"
echo ""
