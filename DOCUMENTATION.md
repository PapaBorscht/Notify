# Система оповещения — Полная документация

**Версия:** 3.0  
**Платформа:** ALT Linux, Python 3.8+  
**Автор:** https://t.me/PapaBorscht

---

## Содержание

1. [Архитектура системы](#1-архитектура-системы)
2. [Два типа уведомлений](#2-два-типа-уведомлений)
3. [Компоненты](#3-компоненты)
4. [Порты и сетевое взаимодействие](#4-порты-и-сетевое-взаимодействие)
5. [Структура файлов и папок](#5-структура-файлов-и-папок)
6. [Как работает доставка уведомлений](#6-как-работает-доставка-уведомлений)
7. [Установка агента](#7-установка-агента)
8. [Установка панели администратора](#8-установка-панели-администратора)
9. [Управление службами](#9-управление-службами)
10. [Логи](#10-логи)
11. [Диагностика](#11-диагностика)
12. [Ansible интеграция](#12-ansible-интеграция)
13. [Настройки](#13-настройки)
14. [Развёртывание через Puppet/Ansible](#14-развёртывание-через-puppetansible)
15. [Автотесты](#15-автотесты)
16. [Справочник файлов и команд](#16-справочник-файлов-и-команд)

---

## 1. Архитектура системы

```
┌─────────────────────────────────────────────────────────┐
│                  Машина администратора                  │
│                                                         │
│  ┌───────────────┐      ┌─────────────────────────┐    │
│  │   Браузер     │◄────►│      server.py           │    │
│  │ localhost:8080│      │  (HTTP сервер панели)    │    │
│  └───────────────┘      │  слушает 0.0.0.0:8080   │    │
│                         └────────────┬────────────┘    │
│                                      │                  │
│  ┌───────────────┐                   │                  │
│  │ Ansible       │──► POST           │                  │
│  │ плейбук       │  /api/ansible     │                  │
│  └───────────────┘  X-API-Key        │                  │
└──────────────────────────────────────┼─────────────────┘
                                       │ HTTP POST :9988
                    ┌──────────────────┼──────────────────┐
                    │                  │                  │
                    ▼                  ▼                  ▼
          ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
          │  PC01        │   │  PC02        │   │  PC03        │
          │  agent.py    │   │  agent.py    │   │  agent.py    │
          │  порт 9988   │   │  порт 9988   │   │  порт 9988   │
          │              │   │              │   │              │
          │ [Fullscreen] │   │ [Popup]      │   │ [Fullscreen] │
          │  весь экран  │   │  снизу-справа│   │  весь экран  │
          └──────────────┘   └──────────────┘   └──────────────┘
```

### Принцип работы

1. Администратор открывает браузер → `http://localhost:8080`
2. Авторизуется логином и паролем
3. Выбирает хосты, тип окна, уровень, вводит текст
4. Нажимает «Отправить» — появляется модал подтверждения со списком хостов
5. Подтверждает → `server.py` параллельно рассылает через `ThreadPoolExecutor`
6. Каждый поток делает HTTP POST на порт `9988` целевой машины
7. `agent.py` проверяет токен, определяет тип окна, показывает уведомление
8. Результат доставки возвращается в панель администратора

---

## 2. Два типа уведомлений

### Fullscreen — полноэкранное окно

Используется для: тревога, атаки, критические события.

- Занимает весь экран (все мониторы)
- Захватывает клавиатуру — нельзя закрыть Alt+F4 или Escape
- Блокирует работу до нажатия кнопки «Принял к сведению»
- Обходит оконный менеджер через `X11BypassWindowManagerHint`

### Popup — всплывающее окно

Используется для: Ansible события, обновления, системные уведомления.

- Маленькое окно в правом нижнем углу экрана
- Не мешает работе пользователя
- Автоматически закрывается через N секунд (прогресс-бар)
- Можно закрыть крестиком досрочно
- Несколько попапов стекируются вертикально

### Логика выбора типа

| Поле `type` | Поле `level` | Результат |
|---|---|---|
| `"fullscreen"` | любой | Fullscreen |
| `"popup"` | любой | Popup |
| не передан | `critical` | Fullscreen |
| не передан | `warning` | Fullscreen |
| не передан | `info` | Popup |

---

## 3. Компоненты

### agent.py v3.0 — Агент уведомлений

Устанавливается на каждую рабочую станцию.

- Живёт в системном трее (иконка с восклицательным знаком)
- HTTP сервер на `0.0.0.0:9988` — принимает уведомления из сети
- Меню трея: статус, счётчик, последнее сообщение, тест, выход
- Два типа окон: `NotifyFullscreen` и `NotifyPopup`
- Логирование в `/tmp/notify-agent-<user>.log`
- Защита от двойного запуска через `flock` lock-файл

### server.py — Веб-сервер панели администратора

- REST API для управления через браузер
- Параллельная рассылка через `ThreadPoolExecutor`
- Два endpoint: `/api/send` (cookie) и `/api/ansible` (X-API-Key)
- Хранит данные в JSON файлах в `data/`
- Настройки рассылки меняются на лету без перезапуска

### sender.py v2.1 — Отправщик

- Вызывается из `server.py` или напрямую из командной строки
- Передаёт `type` и `timeout` агенту только если явно указаны
- При пустом `type` — агент сам определит по `level`

### index.html — Панель администратора (SPA)

Вкладки:
- **Отправить** — выбор хостов, тип окна, уровень, Markdown текст с превью
- **Хосты** — добавление по одному или списком (массовый импорт)
- **Шаблоны** — готовые сообщения
- **История** — журнал всех рассылок
- **Ansible** — конструктор запросов с превью JSON/curl/YAML
- **Настройки** — потоки, timeout, API ключ

---

## 4. Порты и сетевое взаимодействие

| Порт | Компонент | Протокол | Кто слушает | Кто подключается |
|------|-----------|----------|-------------|------------------|
| `8080` | `server.py` | HTTP | Сервер администратора | Браузер, Ansible |
| `9988` | `agent.py` | HTTP | Каждая рабочая станция | `server.py` (через ThreadPool) |

---

## 5. Структура файлов и папок

### Панель администратора

```
/opt/notify-panel/
├── server.py          ← Веб-сервер
├── index.html         ← HTML панель
├── sender.py          ← Отправщик
└── data/
    ├── hosts.json     ← Список хостов
    ├── templates.json ← Шаблоны
    └── history.json   ← История (последние 200)
```

### Агент на рабочих станциях

```
/opt/notify-agent/
├── agent.py           ← Агент (трей + HTTP + окна)
├── sender.py          ← Отправщик (для ручных тестов)
└── agent-start.sh     ← Wrapper-скрипт запуска
```

### Автозапуск и службы

```
/etc/xdg/autostart/notify-agent.desktop   ← XDG автозапуск (все пользователи)
/etc/systemd/user/notify-agent.service    ← Systemd user service
/etc/systemd/system/notify-panel.service  ← Systemd сервис панели
/etc/profile.d/notify-agent.sh            ← QT_QPA_PLATFORMTHEME="" для трея
```

---

## 6. Как работает доставка уведомлений

### Цепочка от кнопки до экрана

```
[Браузер] POST /api/send
    │  {title, message, level, type, timeout, hosts:[...]}
    ▼
[server.py] do_notify()
    │  ThreadPoolExecutor(max_workers=50)
    │  Параллельно для каждого хоста:
    ▼
[server.py] _send_one()
    │  POST http://192.168.1.X:9988
    │  X-Token: supersecrettoken123
    │  {title, message, level, type, timeout}
    ▼
[agent.py] Handler.do_POST()
    │  Проверка X-Token
    │  resolve_type(level, type) → "popup" или "fullscreen"
    │  _notifier.show_message.emit(...)  ← сигнал в Qt main thread
    ▼
[agent.py] TrayApp.on_message()
    │  type == "popup"      → NotifyPopup(title, message, level, timeout)
    │  type == "fullscreen" → NotifyFullscreen(title, message, level)
    ▼
[Экран пользователя]
```

### Параллельная рассылка

По умолчанию 50 потоков, timeout 3 секунды на хост.

| Хостов | Недоступных | Потоков | Время |
|--------|-------------|---------|-------|
| 400 | 30% (120) | 50 | ~24 сек |
| 400 | 30% (120) | 100 | ~12 сек |
| 400 | 30% (120) | 50, timeout=2 | ~16 сек |

Настраивается в панели → вкладка **Настройки**.

---

## 7. Установка агента

### Быстрая установка

```bash
sudo bash install-agent.sh
```

### Что делает скрипт

1. Проверяет наличие Python3 и PyQt5, устанавливает если нет
2. Копирует файлы в `/opt/notify-agent/`
3. Создаёт `/etc/profile.d/notify-agent.sh` с `QT_QPA_PLATFORMTHEME=""`
4. Устанавливает systemd user service в `/etc/systemd/user/`
5. Создаёт `/etc/xdg/autostart/notify-agent.desktop` как резерв
6. Запускает агента в активных X11-сессиях немедленно

### Почему QT_QPA_PLATFORMTHEME=""

На ALT Linux с MATE Qt5 по умолчанию пытается использовать протокол **StatusNotifierItem (SNI)** для трея. MATE его не поддерживает — иконка создаётся но нигде не отображается. Пустая тема принудительно включает старый **XEmbed** протокол который работает везде. Так же делают Яндекс.Браузер и VA Connect.

### Проверка после установки

```bash
# Порт должен слушаться
ss -tlnp | grep 9988

# Лог агента
tail -f /tmp/notify-agent-$(whoami).log

# Тест напрямую
curl -X POST http://127.0.0.1:9988 \
  -H "X-Token: supersecrettoken123" \
  -H "Content-Type: application/json" \
  -d '{"title":"Тест","message":"## Работает!","level":"info"}'
```

---

## 8. Установка панели администратора

```bash
sudo bash install-panel.sh
```

Открыть в браузере: `http://localhost:8080`  
Логин: `admin` / Пароль: `admin123`

---

## 9. Управление службами

### Агент (от имени пользователя, не root)

```bash
systemctl --user status  notify-agent
systemctl --user start   notify-agent
systemctl --user stop    notify-agent
systemctl --user restart notify-agent
journalctl --user -u notify-agent -f
```

### Панель администратора (root)

```bash
systemctl status  notify-panel
systemctl restart notify-panel
journalctl -u notify-panel -f
```

### Перезапуск агента вручную

```bash
pkill -f "agent.py" 2>/dev/null || true
pkill -f "agent-start" 2>/dev/null || true
rm -f /tmp/notify-agent.lock /tmp/notify-agent-start.lock
sleep 2
bash /opt/notify-agent/agent-start.sh &
```

---

## 10. Логи

| Лог | Путь | Что содержит |
|-----|------|--------------|
| Агент | `/tmp/notify-agent-<user>.log` | Старт, полученные сообщения, ошибки Qt |
| Панель (journald) | `journalctl -u notify-panel` | HTTP запросы, ошибки |
| История рассылок | `/opt/notify-panel/data/history.json` | Все отправки с результатами |
| Установка агента | `/var/log/notify-agent-install.log` | Лог установки |

### Что означают записи в логе агента

```
[09:34:57] INFO     === Notify Agent v3.0 старт ===   — агент запустился
[09:34:57] INFO     Трей инициализирован (попытка 1)   — иконка в трее появилась
[09:34:57] INFO     HTTP сервер слушает 0.0.0.0:9988   — готов принимать запросы
[09:36:52] INFO     Получено [warning]: Обновление      — пришло уведомление
[09:36:52] INFO     Показываем [popup] [warning]: ...   — показываем попап
[09:36:55] INFO     Окно закрыто                        — пользователь закрыл
```

---

## 11. Диагностика

### Агент не запускается

```bash
# Смотреть лог
cat /tmp/notify-agent-$(whoami).log

# Проверить порт
ss -tlnp | grep 9988

# Запустить вручную и смотреть ошибки
python3 /opt/notify-agent/agent.py

# Установить PyQt5 если нет
apt-get install python3-module-pyqt5
```

### Два значка в трее

Два экземпляра агента запущены одновременно (два `agent-start.sh`).

```bash
# Убить все копии
pkill -f "agent.py"
pkill -f "agent-start"
rm -f /tmp/notify-agent.lock /tmp/notify-agent-start.lock
sleep 2
# Запустить один
systemctl --user start notify-agent
```

### Уведомление не доходит (HTTP 200 но окно не появляется)

```bash
# Проверить лог агента — видит ли он запрос
tail -f /tmp/notify-agent-$(whoami).log

# Проверить тип — возможно приходит fullscreen вместо popup
# В логе должно быть: "Показываем [popup]" или "Показываем [fullscreen]"
```

### Агент в процессах есть, порта нет

Зомби-процесс. `agent-start.sh` сам обнаружит и перезапустит при следующем запуске. Или вручную:

```bash
pkill -f "agent.py" && rm -f /tmp/notify-agent.lock
sleep 3
ss -tlnp | grep 9988
```

### Ansible возвращает 401

Endpoint `/api/ansible` требует заголовок `X-API-Key`, не cookie.

```bash
# Правильно:
curl -H "X-API-Key: ansible-secret-key" ...

# Неправильно (вызывает 401):
# Нет заголовка X-API-Key
```

---

## 12. Ansible интеграция

### Endpoint /api/ansible

```
POST http://notify-server:8080/api/ansible
X-API-Key: ansible-secret-key
Content-Type: application/json
```

Тело запроса:

```json
{
  "title":   "⚠️ Обновление ядра",
  "message": "## Не выключайте ПК!\n\nИдёт установка.",
  "level":   "warning",
  "type":    "popup",
  "timeout": 10,
  "hosts":   ["192.168.1.10", "192.168.1.11"]
}
```

### Плейбук обновления ядра

```yaml
---
- name: Обновление ядра с уведомлениями
  hosts: workstations
  become: yes
  serial: 1

  tasks:

    - name: Уведомить — начало обновления
      uri:
        url: "http://192.168.0.11:8080/api/ansible"
        method: POST
        headers:
          Content-Type: application/json
          X-API-Key: "ansible-secret-key"
        body_format: json
        body:
          title:   "⚠️ Обновление ядра"
          message: "## Не выключайте компьютер!\n\nНачинается обновление ядра."
          level:   "warning"
          type:    "popup"
          timeout: 15
          hosts:
            - "{{ ansible_host }}"
        status_code: 200
      delegate_to: localhost

    - name: Пауза — дать время прочитать
      pause:
        seconds: 10

    - name: Обновить ядро
      command: update-kernel -y

    - name: Уведомить — готово
      uri:
        url: "http://192.168.0.11:8080/api/ansible"
        method: POST
        headers:
          Content-Type: application/json
          X-API-Key: "ansible-secret-key"
        body_format: json
        body:
          title:   "✅ Обновление завершено"
          message: "## Готово!\n\nСистема перезагружается..."
          level:   "info"
          type:    "popup"
          timeout: 10
          hosts:
            - "{{ ansible_host }}"
        status_code: 200
      delegate_to: localhost

    - name: Перезагрузка
      reboot:
        reboot_timeout: 300
```

### Установка агента через Ansible

```yaml
---
- name: Установка notify-agent
  hosts: workstations
  become: yes

  vars:
    archive_url: "https://gitlab.company.ru/notify-release-final.zip"
    marker_file: "/var/log/ansible-install-notify-agent.done"

  tasks:

    - name: Проверить маркер
      stat:
        path: "{{ marker_file }}"
      register: marker

    - name: Установить если ещё не установлено
      block:
        - name: Установить unzip и PyQt5
          package:
            name: [python3-module-pyqt5, unzip]
            state: present

        - name: Скачать архив
          get_url:
            url:  "{{ archive_url }}"
            dest: "/tmp/notify-release-final.zip"

        - name: Распаковать
          unarchive:
            src:        "/tmp/notify-release-final.zip"
            dest:       "/tmp/"
            remote_src: yes

        - name: Установить
          shell: bash /tmp/release/install-agent.sh

        - name: Создать маркер
          file:
            path:  "{{ marker_file }}"
            state: touch
      when: not marker.stat.exists
```

---

## 13. Настройки

### server.py — постоянные настройки

```python
ADMIN_LOGIN    = "admin"
ADMIN_PASSWORD = "admin123"          # ← поменяй

AGENT_TOKEN    = "supersecrettoken123"  # ← должен совпадать с agent.py

SEND_SETTINGS = {
    "max_workers":  50,   # потоков параллельно
    "send_timeout": 3,    # секунд ждать ответа от агента
}

ANSIBLE_SETTINGS = {
    "api_key": "ansible-secret-key"  # ← поменяй
}
```

### agent.py — настройки агента

```python
PORT                 = 9988               # порт HTTP сервера
TOKEN                = "supersecrettoken123"  # должен совпадать с server.py
POPUP_DEFAULT_TIMEOUT = 8                 # секунд до закрытия попапа
```

### Смена пароля и токена

1. Поменять в `server.py` и `agent.py`
2. Перезапустить оба сервиса
3. Для агента на всех хостах: `sudo bash install-agent.sh` (повторно)

---

## 14. Развёртывание через Puppet/Ansible

### Puppet

```puppet
class notify::agent {
  file { '/opt/notify-agent':
    ensure => directory, mode => '0755',
  }
  file { '/opt/notify-agent/agent.py':
    ensure => present,
    source => 'puppet:///modules/notify/agent.py',
    mode   => '0644',
  }
  file { '/opt/notify-agent/agent-start.sh':
    ensure => present,
    source => 'puppet:///modules/notify/agent-start.sh',
    mode   => '0755',
  }
  file { '/etc/xdg/autostart/notify-agent.desktop':
    ensure => present,
    source => 'puppet:///modules/notify/notify-agent.desktop',
    mode   => '0644',
  }
  file { '/etc/profile.d/notify-agent.sh':
    ensure  => present,
    content => "export QT_QPA_PLATFORMTHEME=\"\"\nexport QT_STYLE_OVERRIDE=\"\"\n",
    mode    => '0644',
  }
}
```

---

## 15. Автотесты

Запуск:
```bash
python3 test_notify.py
```

Результат: **50 тестов, все проходят.**

| Группа | Тестов | Что проверяется |
|--------|--------|-----------------|
| Логика агента | 9 | Markdown→HTML, XSS защита, выбор типа окна |
| Сессии | 7 | Создание, TTL, парсинг cookie |
| Файлы данных | 8 | init_db, jread/jwrite, история ≤200 |
| Sender | 6 | send(), type/timeout в payload, load_hosts |
| HTTP сервер | 20 | Авторизация, все endpoints, Ansible API |

---

## 16. Справочник файлов и команд

### Файлы

| Файл | Путь |
|------|------|
| Агент | `/opt/notify-agent/agent.py` |
| Wrapper запуска | `/opt/notify-agent/agent-start.sh` |
| Сервер панели | `/opt/notify-panel/server.py` |
| Панель (HTML) | `/opt/notify-panel/index.html` |
| Отправщик | `/opt/notify-panel/sender.py` |
| Хосты | `/opt/notify-panel/data/hosts.json` |
| Шаблоны | `/opt/notify-panel/data/templates.json` |
| История | `/opt/notify-panel/data/history.json` |
| XDG автозапуск | `/etc/xdg/autostart/notify-agent.desktop` |
| Systemd агент | `/etc/systemd/user/notify-agent.service` |
| Systemd панель | `/etc/systemd/system/notify-panel.service` |
| QT переменные | `/etc/profile.d/notify-agent.sh` |
| Лог агента | `/tmp/notify-agent-<user>.log` |
| Lock агента | `/tmp/notify-agent.lock` |

### Быстрые команды

```bash
# ── Панель ──
systemctl status  notify-panel
systemctl restart notify-panel
journalctl -u notify-panel -f

# ── Агент ──
systemctl --user status  notify-agent
systemctl --user restart notify-agent
tail -f /tmp/notify-agent-$(whoami).log
ss -tlnp | grep 9988

# ── Тест агента напрямую ──
curl -X POST http://127.0.0.1:9988 \
  -H "X-Token: supersecrettoken123" \
  -H "Content-Type: application/json" \
  -d '{"title":"Тест","message":"## Работает!","level":"info"}'

# ── Тест попапа ──
curl -X POST http://127.0.0.1:9988 \
  -H "X-Token: supersecrettoken123" \
  -H "Content-Type: application/json" \
  -d '{"title":"Попап","message":"Тест","level":"info","type":"popup","timeout":5}'

# ── Тест Ansible endpoint ──
curl -X POST http://localhost:8080/api/ansible \
  -H "X-API-Key: ansible-secret-key" \
  -H "Content-Type: application/json" \
  -d '{"title":"Тест","message":"Ansible работает","level":"info","type":"popup","hosts":["127.0.0.1"]}'

# ── Автотесты ──
cd /opt/notify-panel && python3 test_notify.py
```
