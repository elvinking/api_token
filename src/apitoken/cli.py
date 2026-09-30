"""Консольная утилита для работы с API по токену.

Приоритет источников токена: --token > $APITOKEN_TOKEN > профиль в конфиге.
Приоритет базового URL:      --base-url > $APITOKEN_BASE_URL > профиль в конфиге.
Приоритет профиля:           --profile > $APITOKEN_PROFILE > current_profile > "default".
"""

import argparse
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENV_TOKEN = "APITOKEN_TOKEN"
ENV_BASE_URL = "APITOKEN_BASE_URL"
ENV_CONFIG = "APITOKEN_CONFIG"
ENV_PROFILE = "APITOKEN_PROFILE"

DEFAULT_PROFILE = "default"
# Статусы, при которых повтор осмыслен: перегрузка и временные сбои на стороне сервера.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRY_DELAY_CAP = 30.0


def config_path():
    if os.environ.get(ENV_CONFIG):
        return Path(os.environ[ENV_CONFIG])
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "apitoken" / "config.json"


def migrate(cfg):
    """Конфиг старого формата — плоский {"token": …, "base_url": …}.

    Переносим его в профиль default, чтобы уже сохранённый токен не потерялся.
    """
    if "profiles" in cfg:
        return cfg
    flat = {k: cfg[k] for k in ("token", "base_url") if cfg.get(k)}
    migrated = {"current_profile": DEFAULT_PROFILE, "profiles": {}}
    if flat:
        migrated["profiles"][DEFAULT_PROFILE] = flat
    return migrated


def load_config():
    path = config_path()
    if not path.exists():
        return migrate({})
    try:
        return migrate(json.loads(path.read_text(encoding="utf-8")))
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


def resolve_profile(args, cfg):
    return (getattr(args, "profile", None) or os.environ.get(ENV_PROFILE)
            or cfg.get("current_profile") or DEFAULT_PROFILE)


def get_profile(cfg, name):
    return cfg.get("profiles", {}).get(name, {})


def touch_profile(cfg, name):
    """Возвращает профиль, создавая его. Первый созданный профиль становится текущим."""
    profiles = cfg.setdefault("profiles", {})
    first = not profiles
    prof = profiles.setdefault(name, {})
    if first:
        cfg["current_profile"] = name
    return prof


def resolve_token(args, prof):
    if getattr(args, "token", None):
        return args.token, "--token"
    if os.environ.get(ENV_TOKEN):
        return os.environ[ENV_TOKEN], f"${ENV_TOKEN}"
    if prof.get("token"):
        return prof["token"], str(config_path())
    return None, None


def resolve_base_url(args, prof):
    return getattr(args, "base_url", None) or os.environ.get(ENV_BASE_URL) or prof.get("base_url")


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
    name = resolve_profile(args, cfg)
    prof = touch_profile(cfg, name)
    prof["token"] = token
    if args.base_url:
        prof["base_url"] = args.base_url.rstrip("/")
    save_config(cfg)
    print(f"Токен {mask(token)} сохранён в профиль «{name}» ({config_path()})")


def cmd_logout(args):
    cfg = load_config()
    name = resolve_profile(args, cfg)
    profiles = cfg.get("profiles", {})
    if args.purge:
        if profiles.pop(name, None) is None:
            print(f"Профиля «{name}» нет.")
            return
        if cfg.get("current_profile") == name:
            cfg["current_profile"] = DEFAULT_PROFILE
        save_config(cfg)
        print(f"Профиль «{name}» удалён.")
        return
    if profiles.get(name, {}).pop("token", None) is None:
        print(f"В профиле «{name}» сохранённого токена нет.")
        return
    save_config(cfg)
    print(f"Токен профиля «{name}» удалён.")


def cmd_status(args):
    cfg = load_config()
    name = resolve_profile(args, cfg)
    prof = get_profile(cfg, name)
    token, source = resolve_token(args, prof)
    print(f"Конфиг:      {config_path()}")
    print(f"Профиль:     {name}")
    print(f"Базовый URL: {resolve_base_url(args, prof) or '(не задан)'}")
    if token:
        print(f"Токен:       {mask(token)} (из {source})")
    else:
        print("Токен:       не задан — выполните `apitoken login`")
        return 1


def cmd_profiles(args):
    cfg = load_config()
    profiles = cfg.get("profiles", {})
    if not profiles:
        print("Профилей нет — выполните `apitoken login`.")
        return 1
    current = resolve_profile(args, cfg)
    for name in sorted(profiles):
        prof = profiles[name]
        token = prof.get("token")
        print(f"{'*' if name == current else ' '} {name}: "
              f"токен {mask(token) if token else '(не задан)'}, "
              f"URL {prof.get('base_url') or '(не задан)'}")


def cmd_use(args):
    cfg = load_config()
    if args.name not in cfg.get("profiles", {}):
        known = ", ".join(sorted(cfg.get("profiles", {}))) or "—"
        raise SystemExit(f"Профиля «{args.name}» нет. Доступные: {known}")
    cfg["current_profile"] = args.name
    save_config(cfg)
    print(f"Текущий профиль: {args.name}")


def cmd_set_url(args):
    cfg = load_config()
    name = resolve_profile(args, cfg)
    prof = touch_profile(cfg, name)
    prof["base_url"] = args.url.rstrip("/")
    save_config(cfg)
    print(f"Базовый URL профиля «{name}»: {prof['base_url']}")


def parse_query(items):
    pairs = []
    for item in items or []:
        name, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"Некорректный параметр {item!r}, нужен формат 'ключ=значение'.")
        pairs.append((name, value))
    return pairs


def build_url(base_url, path, query=None):
    if path.startswith(("http://", "https://")):
        url = path
    elif not base_url:
        raise SystemExit("Базовый URL не задан: `apitoken set-url https://api.example.com` или полный URL.")
    else:
        url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    if query:
        parts = urllib.parse.urlsplit(url)
        # Параметры из пути сохраняем и дописываем к ним -q, а не затираем.
        merged = urllib.parse.parse_qsl(parts.query, keep_blank_values=True) + list(query)
        url = urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(merged)))
    return url


def retry_after(headers, fallback):
    """Retry-After в секундах. Форма с датой (RFC 1123) не поддерживается — берём fallback."""
    raw = headers.get("Retry-After") if headers else None
    if raw:
        try:
            return min(max(0.0, float(raw.strip())), RETRY_DELAY_CAP)
        except ValueError:
            pass
    return fallback


def perform(req, args):
    """Выполняет запрос, повторяя временные сбои. Возвращает (status, headers, payload)."""
    attempts = args.retries + 1
    for attempt in range(1, attempts + 1):
        last = attempt == attempts
        delay = min(args.retry_delay * (2 ** (attempt - 1)), RETRY_DELAY_CAP)
        try:
            with urllib.request.urlopen(req, timeout=args.timeout) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            # HTTPError — подкласс URLError, поэтому ловится первым.
            status, headers, payload = e.code, e.headers, e.read()
            if last or status not in RETRY_STATUSES:
                return status, headers, payload
            wait = retry_after(headers, delay)
            print(f"HTTP {status} — повтор {attempt}/{args.retries} через {wait:g} с", file=sys.stderr)
        except urllib.error.URLError as e:
            if last:
                raise SystemExit(f"Ошибка соединения: {e.reason}")
            wait = delay
            print(f"Ошибка соединения ({e.reason}) — повтор {attempt}/{args.retries} через {wait:g} с",
                  file=sys.stderr)
        time.sleep(wait)


def cmd_request(args):
    if args.retries < 0:
        raise SystemExit("--retries не может быть отрицательным.")
    cfg = load_config()
    name = resolve_profile(args, cfg)
    prof = get_profile(cfg, name)
    token, _ = resolve_token(args, prof)
    if not token:
        raise SystemExit("Токен не задан: выполните `apitoken login` или задайте $APITOKEN_TOKEN.")

    url = build_url(resolve_base_url(args, prof), args.path, parse_query(args.query))
    headers = {"Accept": "application/json"}
    headers[args.auth_header] = f"Bearer {token}" if args.auth_header == "Authorization" else token
    for h in args.header or []:
        hname, sep, value = h.partition(":")
        if not sep:
            raise SystemExit(f"Некорректный заголовок {h!r}, нужен формат 'Имя: значение'.")
        headers[hname.strip()] = value.strip()

    body = None
    if args.data is not None:
        # stdin читаем один раз, до повторов — иначе на втором заходе тело окажется пустым.
        data = sys.stdin.read() if args.data == "-" else args.data
        body = data.encode("utf-8")
        headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=body, headers=headers, method=args.method.upper())
    status, resp_headers, payload = perform(req, args)

    if args.verbose:
        print(f"HTTP {status}", file=sys.stderr)
    if args.include:
        print(f"HTTP {status}")
        for key, value in (resp_headers or {}).items():
            print(f"{key}: {value}")
        print()

    if args.output:
        Path(args.output).write_bytes(payload)
        print(f"{len(payload)} байт -> {args.output}", file=sys.stderr)
    else:
        text = payload.decode("utf-8", errors="replace")
        if not args.raw:
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            except ValueError:
                pass
        print(text)
    return 0 if status < 400 else 1


def build_parser():
    p = argparse.ArgumentParser(prog="apitoken", description="Работа с API по токену.")
    p.add_argument("--token", help="токен (перекрывает сохранённый и $APITOKEN_TOKEN)")
    p.add_argument("--base-url", help="базовый URL API")
    p.add_argument("--profile", help=f"имя профиля (перекрывает ${ENV_PROFILE} и текущий)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("login", help="сохранить токен в профиль")
    s.add_argument("login_token", nargs="?", metavar="token",
                   help="токен; если не указан — запрос с клавиатуры или stdin")
    s.set_defaults(func=cmd_login)

    s = sub.add_parser("logout", help="удалить сохранённый токен")
    s.add_argument("--purge", action="store_true", help="удалить профиль целиком, а не только токен")
    s.set_defaults(func=cmd_logout)

    s = sub.add_parser("status", help="показать текущие настройки")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("profiles", help="список профилей ('*' — текущий)")
    s.set_defaults(func=cmd_profiles)

    s = sub.add_parser("use", help="переключить текущий профиль")
    s.add_argument("name")
    s.set_defaults(func=cmd_use)

    s = sub.add_parser("set-url", help="сохранить базовый URL API")
    s.add_argument("url")
    s.set_defaults(func=cmd_set_url)

    s = sub.add_parser("request", help="выполнить запрос к API с токеном")
    s.add_argument("method", help="GET, POST, PUT, PATCH, DELETE…")
    s.add_argument("path", help="путь относительно базового URL или полный URL")
    s.add_argument("-d", "--data", help="тело запроса (JSON); '-' — читать из stdin")
    s.add_argument("-H", "--header", action="append", help="доп. заголовок 'Имя: значение'")
    s.add_argument("-q", "--query", action="append", help="параметр строки запроса 'ключ=значение'")
    s.add_argument("-i", "--include", action="store_true", help="напечатать статус и заголовки ответа")
    s.add_argument("-o", "--output", help="записать тело ответа в файл вместо stdout")
    s.add_argument("--raw", action="store_true", help="не форматировать JSON-ответ")
    s.add_argument("--auth-header", default="Authorization",
                   help="заголовок для токена (по умолчанию Authorization: Bearer <токен>)")
    s.add_argument("--retries", type=int, default=0,
                   help=f"повторы при сбоях сети и статусах {sorted(RETRY_STATUSES)} (по умолчанию 0)")
    s.add_argument("--retry-delay", type=float, default=1.0,
                   help="базовая пауза перед повтором в секундах, растёт вдвое (по умолчанию 1)")
    s.add_argument("--timeout", type=float, default=30)
    s.add_argument("-v", "--verbose", action="store_true", help="печатать HTTP-статус в stderr")
    s.set_defaults(func=cmd_request)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args) or 0
    except BrokenPipeError:
        # Получатель закрыл поток раньше времени (`apitoken request … | head`).
        # Перенаправляем stdout в /dev/null, иначе интерпретатор на выходе
        # попытается сбросить буфер и напечатает "Exception ignored".
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError):
            pass
        return 141  # 128 + SIGPIPE, как у обычных консольных утилит
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130  # 128 + SIGINT


if __name__ == "__main__":
    sys.exit(main())
