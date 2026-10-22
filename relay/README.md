# Релей к Groq на Cloudflare Workers

**Зачем:** `api.groq.com` отвечает `403` с российских IP. Релей на Cloudflare
принимает запрос из РФ и уходит в Groq уже с зарубежного edge. VPN не нужен,
ключ Groq остаётся в секретах Worker.

Бесплатный тариф Workers: 100 000 запросов в день — с запасом для голосового
ассистента (у Groq на free-плане всё равно 1 000 запросов в день).

---

## Вариант 1: панель Cloudflare (без Node.js, ~5 минут)

1. Зарегистрируйтесь на [dash.cloudflare.com](https://dash.cloudflare.com)
   (карта не нужна).
2. **Workers & Pages → Create → Worker**.
3. Назовите `gul-groq-relay` → **Deploy**.
4. **Edit code** → удалите всё и вставьте содержимое `groq-relay.js` → **Deploy**.
5. **Settings → Variables and Secrets → Add**:
   - `GROQ_API_KEY` — ключ с [console.groq.com/keys](https://console.groq.com/keys), тип **Secret**;
   - `RELAY_TOKEN` — придумайте длинный пароль (например, `openssl rand -hex 24`), тип **Secret**.
6. Скопируйте адрес вида
   `https://gul-groq-relay.ваш-аккаунт.workers.dev`.

Проверка:

```bash
curl https://gul-groq-relay.ваш-аккаунт.workers.dev/health
# {"ok":true,"key_configured":true,"token_required":true,...}

curl -H "Authorization: Bearer ВАШ_RELAY_TOKEN" \
  https://gul-groq-relay.ваш-аккаунт.workers.dev/openai/v1/models
# {"object":"list","data":[{"id":"openai/gpt-oss-20b",...}, ...]}
```

Если видите список моделей — релей работает.

---

## Вариант 2: Wrangler CLI

```bash
cd relay
npx wrangler deploy
npx wrangler secret put GROQ_API_KEY
npx wrangler secret put RELAY_TOKEN
```

---

## Подключение к Гулю

В `.env` на тестовом ПК или на Acer:

```dotenv
GROQ_RELAY_URL=https://gul-groq-relay.ваш-аккаунт.workers.dev/openai/v1
GROQ_RELAY_TOKEN=ваш_длинный_пароль
```

`config.yaml` уже читает эти переменные, менять его не нужно. Прямой
`GROQ_API_KEY` при работе через релей не требуется.

Проверка:

```bash
python run.py --brain groq
python run.py --doctor
```

Ожидаемый вывод: `Groq (через свой релей) — OK, первый токен NNN мс`.

---

## Безопасность и честные оговорки

- **Обязательно задайте `RELAY_TOKEN`.** Без него релей открыт всему интернету,
  и вашу квоту Groq сможет тратить кто угодно.
- `GROQ_API_KEY` живёт только в секретах Worker. В коде, логах и на неттопе его нет.
- Релей принимает только два пути (`chat/completions` и `models`) — это не
  открытый прокси, а узкий мост к одному API с вашим собственным ключом.
  Не делайте его публичным сервисом и не раздавайте токен: массовая раздача
  чужих API через Workers нарушает условия Cloudflare.
- Cloudflare может быть недоступен у отдельных провайдеров в РФ. Если релей
  не отвечает — проверьте с телефона по мобильному интернету. Альтернатива:
  VPN/прокси и прямой доступ к Groq (`network.proxy` в `config.yaml`).
- Если Cloudflare или Groq недоступны совсем — Гуль автоматически переключится
  на GigaChat (из РФ, без VPN) или Cloudflare Workers AI. Разговор не сломается.

---

## Диагностика

| Симптом | Причина | Что делать |
|---|---|---|
| `/health` → `key_configured: false` | не добавлен секрет | Settings → Variables → `GROQ_API_KEY` |
| `403 forbidden` от релея | неверный или отсутствующий `RELAY_TOKEN` | сверьте `.env` и секрет Worker |
| `401` от Groq | ключ неверный/отозван | перевыпустите ключ на console.groq.com |
| `429` от Groq | лимит 30/мин, 1000/день, 8K токенов/мин | подождите, уменьшите `max_tokens`, см. ниже |
| `502 upstream_unreachable` | Worker не смог достучаться до Groq | повторите; проверьте статус Groq |
| `404 not_found` | неверный URL релея | должен заканчиваться на `/openai/v1` |
| Долгие ответы | reasoning съедает токены | уже стоит `reasoning_effort: low` |

### Про лимиты Groq

На free-плане `openai/gpt-oss-20b` и `openai/gpt-oss-120b`:

| Лимит | Значение |
|---|---|
| Запросов в минуту | 30 |
| Запросов в день | 1 000 |
| Токенов в минуту | 8 000 |
| Токенов в день | 200 000 |

Для голосового ассистента это ~8–15 коротких ответов в минуту. Если упираетесь
в 8K TPM — уменьшите `brain.max_tokens` до 250–300 и `max_history_turns` до 6:
история тоже расходует токены. Гуль сам поставит Groq на паузу после `429` и
ответит через GigaChat, а через 45 секунд вернётся к Groq.
