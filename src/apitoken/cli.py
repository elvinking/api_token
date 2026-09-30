"""Консольная утилита для работы с API по токену.

Приоритет источников токена: --token > $APITOKEN_TOKEN > файл конфигурации.
Приоритет базового URL:      --base-url > $APITOKEN_BASE_URL > файл конфигурации.
"""

import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENV_TOKEN = "APITOKEN_TOKEN"
ENV_BASE_URL = "APITOKEN_BASE_URL"
ENV_CONFIG = "APITOKEN_CONFIG"


def config_path():
    if os.environ.get(ENV_CONFIG):
        return Path(os.environ[ENV_CONFIG])
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "apitoken" / "config.json"


def load_config():
    path = config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"Не удалось прочитать конфиг {path}: {e}")


def save_config(cfg):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Файл создаётся сразу с правами 600, чтобы токен не был виден другим пользователям.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)


def resolve_token(args, cfg):
    if getattr(args, "token", None):
        return args.token, "--token"
    if os.environ.get(ENV_TOKEN):
        return os.environ[ENV_TOKEN], f"${ENV_TOKEN}"
    if cfg.get("token"):
        return cfg["token"], str(config_path())
    return None, None


def resolve_base_url(args, cfg):
    return getattr(args, "base_url", None) or os.environ.get(ENV_BASE_URL) or cfg.get("base_url")


def mask(token):
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}…{token[-4:]}"


def cmd_login(args):
    token = args.login_token or args.token
    if token is None:
        if sys.stdin.isatty():
            token = getpass.getpass("Токен: ")
        else:
            token = sys.stdin.read()
    token = token.strip()
    if not token:
        raise SystemExit("Пустой токен — ничего не сохранено.")
    cfg = load_config()
    cfg["token"] = token
    if args.base_url:
        cfg["base_url"] = args.base_url.rstrip("/")
    save_config(cfg)
    print(f"Токен {mask(token)} сохранён в {config_path()}")


def cmd_logout(args):
    cfg = load_config()
    if cfg.pop("token", None) is None:
        print("Сохранённого токена нет.")
        return
    save_config(cfg)
    print("Токен удалён.")


def cmd_status(args):
    cfg = load_config()
    token, source = resolve_token(args, cfg)
    print(f"Конфиг:     {config_path()}")
    print(f"Базовый URL: {resolve_base_url(args, cfg) or '(не задан)'}")
    if token:
        print(f"Токен:      {mask(token)} (из {source})")
    else:
        print("Токен:      не задан — выполните `apitoken login`")
        return 1


def cmd_set_url(args):
    cfg = load_config()
    cfg["base_url"] = args.url.rstrip("/")
    save_config(cfg)
    print(f"Базовый URL: {cfg['base_url']}")


def build_url(base_url, path):
    if path.startswith(("http://", "https://")):
        return path
    if not base_url:
        raise SystemExit("Базовый URL не задан: `apitoken set-url https://api.example.com` или полный URL.")
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def cmd_request(args):
    cfg = load_config()
    token, _ = resolve_token(args, cfg)
    if not token:
        raise SystemExit("Токен не задан: выполните `apitoken login` или задайте $APITOKEN_TOKEN.")

    url = build_url(resolve_base_url(args, cfg), args.path)
    headers = {"Accept": "application/json"}
    headers[args.auth_header] = f"Bearer {token}" if args.auth_header == "Authorization" else token
    for h in args.header or []:
        name, sep, value = h.partition(":")
        if not sep:
            raise SystemExit(f"Некорректный заголовок {h!r}, нужен формат 'Имя: значение'.")
        headers[name.strip()] = value.strip()

    body = None
    if args.data is not None:
        data = sys.stdin.read() if args.data == "-" else args.data
        body = data.encode("utf-8")
        headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=body, headers=headers, method=args.method.upper())
    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            status, payload = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, payload = e.code, e.read()
    except urllib.error.URLError as e:
        raise SystemExit(f"Ошибка соединения: {e.reason}")

    text = payload.decode("utf-8", errors="replace")
    try:
        text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
    except ValueError:
        pass
    if args.verbose:
        print(f"HTTP {status}", file=sys.stderr)
    print(text)
    return 0 if status < 400 else 1


def build_parser():
    p = argparse.ArgumentParser(prog="apitoken", description="Работа с API по токену.")
    p.add_argument("--token", help="токен (перекрывает сохранённый и $APITOKEN_TOKEN)")
    p.add_argument("--base-url", help="базовый URL API")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("login", help="сохранить токен")
    s.add_argument("login_token", nargs="?", metavar="token", help="токен; если не указан — запрос с клавиатуры или stdin")
    s.set_defaults(func=cmd_login)

    s = sub.add_parser("logout", help="удалить сохранённый токен")
    s.set_defaults(func=cmd_logout)

    s = sub.add_parser("status", help="показать текущие настройки")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("set-url", help="сохранить базовый URL API")
    s.add_argument("url")
    s.set_defaults(func=cmd_set_url)

    s = sub.add_parser("request", help="выполнить запрос к API с токеном")
    s.add_argument("method", help="GET, POST, PUT, PATCH, DELETE…")
    s.add_argument("path", help="путь относительно базового URL или полный URL")
    s.add_argument("-d", "--data", help="тело запроса (JSON); '-' — читать из stdin")
    s.add_argument("-H", "--header", action="append", help="доп. заголовок 'Имя: значение'")
    s.add_argument("--auth-header", default="Authorization",
                   help="заголовок для токена (по умолчанию Authorization: Bearer <токен>)")
    s.add_argument("--timeout", type=float, default=30)
    s.add_argument("-v", "--verbose", action="store_true", help="печатать HTTP-статус в stderr")
    s.set_defaults(func=cmd_request)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
