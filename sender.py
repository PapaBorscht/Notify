#!/usr/bin/env python3
"""
Отправщик уведомлений v2.1
Поддерживает type (fullscreen/popup) и timeout.
type передаётся агенту только если непустой — агент сам определит по level.
"""
import sys
import json
import argparse
import urllib.request
import urllib.error
from pathlib import Path

PORT       = 9988
TOKEN      = "supersecrettoken123"
HOSTS_FILE = Path(__file__).parent / "data" / "hosts.json"


def send(title: str, message: str, level: str,
         msg_type: str, timeout: int, host: str) -> bool:
    url = f"http://{host}:{PORT}"

    payload = {"title": title, "message": message, "level": level}
    # type и timeout добавляем только если type явно указан
    # Если пустой — агент сам определит тип по level
    if msg_type:
        payload["type"]    = msg_type
        payload["timeout"] = timeout

    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        url, data=data,
        headers={
            "Content-Type":   "application/json",
            "X-Token":        TOKEN,
            "Content-Length": str(len(data)),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except urllib.error.URLError as e:
        print(f"  ✗ {host}: {e.reason}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"  ✗ {host}: {e}", file=sys.stderr)
        return False


def load_hosts() -> list:
    try:
        return [h["address"] for h in
                json.loads(HOSTS_FILE.read_text(encoding="utf-8"))
                if h.get("enabled", True)]
    except Exception as e:
        print(f"Не удалось загрузить хосты: {e}", file=sys.stderr)
        return []


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Отправка уведомления агентам")
    ap.add_argument("title")
    ap.add_argument("message")
    ap.add_argument("--host",    metavar="IP",    default=None)
    ap.add_argument("--level",   default="info",
                    choices=["info", "warning", "critical"])
    ap.add_argument("--type",    dest="msg_type", default="",
                    choices=["", "fullscreen", "popup"])
    ap.add_argument("--timeout", type=int,        default=8)
    args = ap.parse_args()

    hosts = [args.host] if args.host else load_hosts()
    if not hosts:
        print("Нет хостов для отправки.", file=sys.stderr)
        sys.exit(1)

    ok = sum(
        send(args.title, args.message, args.level,
             args.msg_type, args.timeout, h)
        for h in hosts
    )
    sys.exit(0 if ok > 0 else 1)
