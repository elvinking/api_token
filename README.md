# apitoken

Консольная команда для работы с API по токену. Только стандартная библиотека Python (3.8+).

## Установка

```bash
pip install .          # или: pipx install .
apitoken --help
```

Без установки: `PYTHONPATH=src python3 -m apitoken --help`.

## Токен

Настройка с нуля — три команды:

```bash
pip install .
apitoken login        # ввести токен по запросу; ввод не отображается
apitoken status       # проверка: токен виден в маскированном виде
```

```bash
apitoken login                    # спросит токен скрыто (не попадёт в историю shell)
echo "$MY_TOKEN" | apitoken login # из stdin — удобно в CI
apitoken status                   # показать токен (замаскирован) и откуда он взят
apitoken logout                   # удалить токен из профиля
apitoken logout --purge           # удалить профиль целиком
```

Токен нельзя передавать аргументом в неинтерактивной среде (`apitoken login <токен>`
попадает и в историю shell, и в вывод `ps`). Вместо этого — скрытый ввод или stdin:

```bash
apitoken login <<'EOF'
<токен>
EOF
```

Токен хранится в `~/.config/apitoken/config.json` с правами `600`. Порядок поиска:

1. `apitoken --token <токен> …`
2. переменная окружения `APITOKEN_TOKEN`
3. сохранённый через `apitoken login` (текущий профиль)

Путь к конфигу можно переопределить через `APITOKEN_CONFIG`.

Токен не должен попадать в репозиторий: в коммиты, CI-конфиги и `pyproject.toml`
его не добавляют — для CI используйте секрет, проброшенный в `$APITOKEN_TOKEN`.

## Профили

Несколько окружений (прод, стейдж, разные аккаунты) — это профили: у каждого свой
токен и свой базовый URL.

```bash
apitoken --profile staging login        # сохранить токен в профиль staging
apitoken --profile staging set-url https://api.staging.example.com
apitoken profiles                       # список; '*' — текущий
apitoken use staging                    # переключить текущий профиль
apitoken --profile staging request GET /users/me   # разово, без переключения
```

Порядок выбора профиля: `--profile` > `$APITOKEN_PROFILE` > текущий из конфига > `default`.
Первый созданный профиль автоматически становится текущим.

Конфиг старого формата (плоский `{"token": …}`) переносится в профиль `default`
автоматически при первом чтении — уже сохранённый токен не теряется.

## Запросы

```bash
apitoken set-url https://api.example.com   # или $APITOKEN_BASE_URL / --base-url
apitoken request GET /users/me
apitoken request POST /items -d '{"name": "test"}'
cat body.json | apitoken request PUT /items/1 -d -
apitoken request GET /x -H 'X-Trace: 1' -v
```

| Флаг | Назначение |
| --- | --- |
| `-d, --data` | тело запроса (JSON); `-` — читать из stdin |
| `-H, --header` | доп. заголовок `'Имя: значение'` |
| `-q, --query` | параметр строки запроса `'ключ=значение'`, экранируется автоматически |
| `-i, --include` | напечатать статус и заголовки ответа |
| `-o, --output` | записать тело ответа в файл вместо stdout |
| `--raw` | не форматировать JSON-ответ |
| `--auth-header` | заголовок для токена (по умолчанию `Authorization`) |
| `--retries` | повторы при сбоях (по умолчанию 0) |
| `--retry-delay` | базовая пауза перед повтором, растёт вдвое (по умолчанию 1 с) |
| `--timeout` | таймаут запроса (по умолчанию 30 с) |
| `-v, --verbose` | печатать HTTP-статус в stderr |

По умолчанию токен отправляется как `Authorization: Bearer <токен>`. Если API ждёт его
в другом заголовке — `--auth-header X-API-Key` (тогда токен передаётся без `Bearer`).

Параметры `-q` дописываются к тем, что уже есть в пути, а не затирают их:

```bash
apitoken request GET '/search?page=1' -q 'q=привет мир' -q 'limit=10'
# -> /search?page=1&q=%D0%BF%D1%80%D0%B8%D0%B2%D0%B5%D1%82+%D0%BC%D0%B8%D1%80&limit=10
```

### Повторы

`--retries N` повторяет запрос при сетевых сбоях и статусах `429, 500, 502, 503, 504`.
Пауза растёт вдвое от `--retry-delay` и ограничена 30 секундами; если сервер прислал
`Retry-After` в секундах, используется он. Тело из stdin читается один раз, до повторов,
поэтому не теряется на второй попытке.

```bash
apitoken request GET /flaky --retries 3 --retry-delay 0.5
```

Повторяются только временные сбои — `4xx` (кроме `429`) возвращаются сразу.

## Коды возврата

| Код | Значение |
| --- | --- |
| `0` | успех |
| `1` | сервер ответил статусом ≥ 400, либо ошибка конфигурации/аргументов |
| `130` | прервано с клавиатуры (Ctrl-C) |
| `141` | получатель закрыл поток (`apitoken request … \| head`) |

## Тесты

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```
