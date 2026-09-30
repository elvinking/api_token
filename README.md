# apitoken

Консольная команда для работы с API по токену. Только стандартная библиотека Python (3.8+).

## Установка

```bash
pip install .          # или: pipx install .
apitoken --help
```

Без установки: `PYTHONPATH=src python3 -m apitoken --help`.

## Токен

```bash
apitoken login                    # спросит токен скрыто (не попадёт в историю shell)
echo "$MY_TOKEN" | apitoken login # из stdin — удобно в CI
apitoken status                   # показать токен (замаскирован) и откуда он взят
apitoken logout                   # удалить сохранённый токен
```

Настройка с нуля — три команды:

```bash
pip install .
apitoken login        # ввести токен по запросу; ввод не отображается
apitoken status       # проверка: токен виден в маскированном виде
```

Токен нельзя передавать аргументом в неинтерактивной среде (`apitoken login <токен>`
попадает и в историю shell, и в вывод `ps`). Вместо этого — скрытый ввод или stdin:

```bash
apitoken login <<'EOF'
<токен>
EOF
```

Токен не должен попадать в репозиторий: он хранится только в `~/.config/apitoken/config.json`
(права `600`) либо в переменной окружения. В коммиты, CI-конфиги и `pyproject.toml`
его не добавляют — для CI используйте секрет, проброшенный в `$APITOKEN_TOKEN`.

Токен хранится в `~/.config/apitoken/config.json` с правами `600`.

Порядок, в котором ищется токен:

1. `apitoken --token <токен> …`
2. переменная окружения `APITOKEN_TOKEN`
3. сохранённый через `apitoken login`

Путь к конфигу можно переопределить через `APITOKEN_CONFIG`.

## Запросы

```bash
apitoken set-url https://api.example.com   # или $APITOKEN_BASE_URL / --base-url
apitoken request GET /users/me
apitoken request POST /items -d '{"name": "test"}'
cat body.json | apitoken request PUT /items/1 -d -
apitoken request GET /x -H 'X-Trace: 1' -v
```

По умолчанию токен отправляется как `Authorization: Bearer <токен>`. Если API ждёт его
в другом заголовке — `--auth-header X-API-Key` (тогда токен передаётся без `Bearer`).

Команда завершается с кодом `1`, если сервер ответил статусом ≥ 400.

## Тесты

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```
