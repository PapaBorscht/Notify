#!/bin/bash
# ================================================================
# Wrapper-скрипт запуска агента уведомлений v2.3
# Единственная проверка — порт 9988.
# Shell-level lock предотвращает два параллельных agent-start.sh
# ================================================================
AGENT="/opt/notify-agent/agent.py"
LOG="/tmp/notify-agent-$(id -un).log"
LOCK="/tmp/notify-agent.lock"
SHELL_LOCK="/tmp/notify-agent-start.lock"
PYTHON="/usr/bin/python3"

touch "$LOG"

# ── Shell-level lock — только один agent-start.sh за раз ──
# Используем fd 200 и flock — атомарная операция
exec 200>"$SHELL_LOCK"
if ! flock -n 200; then
    echo "[$(date '+%H:%M:%S')] agent-start.sh уже запущен, выходим" >> "$LOG"
    exit 0
fi

echo "[$(date '+%H:%M:%S')] agent-start.sh запущен (PID=$$)" >> "$LOG"

# ── Единственная проверка — порт 9988 ──
if ss -tlnp 2>/dev/null | grep -q ":9988"; then
    echo "[$(date '+%H:%M:%S')] Порт 9988 занят — агент уже работает, выходим" >> "$LOG"
    exit 0
fi

# Порт свободен — убиваем всё старое
pkill -u "$(id -un)" -f "agent.py" 2>/dev/null || true
rm -f "$LOCK"
sleep 1

# ── Ждать готовности X11 (до 60 сек) ──
echo "[$(date '+%H:%M:%S')] Ожидание X11..." >> "$LOG"
for i in $(seq 1 60); do
    xprop -root > /dev/null 2>&1 && break
    sleep 1
done

if ! xprop -root > /dev/null 2>&1; then
    echo "[$(date '+%H:%M:%S')] X11 недоступен после 60 сек, выход" >> "$LOG"
    exit 1
fi
echo "[$(date '+%H:%M:%S')] X11 готов" >> "$LOG"

# ── Ждать загрузки рабочего стола (панели, трей) ──
# Без этой паузы агент стартует до загрузки GNOME-панелей
# и геометрия экрана возвращается неверной (без учёта панелей)
echo "[$(date '+%H:%M:%S')] Ожидание загрузки рабочего стола (8 сек)..." >> "$LOG"
sleep 8

# ── Переменные окружения ──
export QT_QPA_PLATFORMTHEME=""
export QT_STYLE_OVERRIDE=""
export QT_AUTO_SCREEN_SCALE_FACTOR=0
[ -z "$DISPLAY" ]    && export DISPLAY=":0"
[ -z "$XAUTHORITY" ] && export XAUTHORITY="$HOME/.Xauthority"
if [ -z "$DBUS_SESSION_BUS_ADDRESS" ]; then
    UID_NUM=$(id -u)
    [ -S "/run/user/${UID_NUM}/bus" ] && \
        export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${UID_NUM}/bus"
fi

echo "[$(date '+%H:%M:%S')] Запуск агента (DISPLAY=$DISPLAY)" >> "$LOG"

# ── Цикл с перезапуском при падении ──
while true; do

    if ss -tlnp 2>/dev/null | grep -q ":9988"; then
        echo "[$(date '+%H:%M:%S')] Порт 9988 занят перед стартом, выходим" >> "$LOG"
        exit 0
    fi

    echo "[$(date '+%H:%M:%S')] Запускаем агента..." >> "$LOG"
    "$PYTHON" "$AGENT" >> "$LOG" 2>&1
    EXIT_CODE=$?

    if [ "$EXIT_CODE" -eq 0 ]; then
        echo "[$(date '+%H:%M:%S')] Агент завершился штатно (код 0), выходим" >> "$LOG"
        rm -f "$LOCK"
        exit 0
    fi

    echo "[$(date '+%H:%M:%S')] Агент упал (код $EXIT_CODE), перезапуск через 5 сек..." >> "$LOG"
    rm -f "$LOCK"
    sleep 5

done
