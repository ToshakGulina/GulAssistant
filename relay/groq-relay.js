/**
 * Гуль — релей к Groq через Cloudflare Workers.
 *
 * Зачем: api.groq.com отдаёт 403 с российских IP. Worker работает на edge
 * Cloudflare, поэтому запрос уходит уже не из РФ. Ключ Groq хранится только
 * в секретах Worker — на неттоп он не попадает.
 *
 * Что делает:
 *   POST /openai/v1/chat/completions  → api.groq.com/openai/v1/chat/completions
 *   GET  /openai/v1/models            → список моделей (проверка ключа)
 *   GET  /health                      → состояние релея (без секретов)
 *
 * Стриминг SSE пробрасывается как есть, поэтому потоковая озвучка Гуля
 * работает без изменений.
 *
 * Секреты (Settings → Variables and Secrets, тип Secret):
 *   GROQ_API_KEY  — ключ с console.groq.com/keys (обязательно)
 *   RELAY_TOKEN   — свой пароль для релея (настоятельно рекомендуется)
 */

const GROQ_ORIGIN = "https://api.groq.com";
// Переопределение нужно только для локальных тестов релея (relay/test.mjs).
const upstreamOrigin = (env) => env.GROQ_ORIGIN || GROQ_ORIGIN;
const ALLOWED = [
  /^openai\/v1\/chat\/completions$/,
  /^openai\/v1\/models$/,
];

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization, Content-Type, X-Relay-Token",
  "Access-Control-Max-Age": "86400",
};

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...CORS },
  });
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS });
    }

    const url = new URL(request.url);
    let path = url.pathname.replace(/^\/+/, "").replace(/\/+$/, "");

    if (path === "health" || path === "") {
      return json({
        ok: true,
        service: "gul-groq-relay",
        key_configured: Boolean(env.GROQ_API_KEY),
        token_required: Boolean(env.RELAY_TOKEN),
        upstream: upstreamOrigin(env),
      });
    }

    // Разрешаем и короткий путь (/chat/completions), и полный.
    if (!path.startsWith("openai/v1/")) {
      path = `openai/v1/${path}`;
    }
    if (!ALLOWED.some((pattern) => pattern.test(path))) {
      return json(
        { error: "not_found", hint: "Разрешены только /openai/v1/chat/completions и /openai/v1/models" },
        404,
      );
    }

    if (env.RELAY_TOKEN) {
      const auth = (request.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "").trim();
      const token = auth || request.headers.get("X-Relay-Token") || "";
      if (token !== env.RELAY_TOKEN) {
        return json(
          { error: "forbidden", hint: "Неверный RELAY_TOKEN. Проверьте GROQ_RELAY_TOKEN в .env" },
          403,
        );
      }
    }

    if (!env.GROQ_API_KEY) {
      return json(
        { error: "not_configured", hint: "Добавьте секрет GROQ_API_KEY в настройках Worker" },
        500,
      );
    }

    const headers = new Headers();
    headers.set("Authorization", `Bearer ${env.GROQ_API_KEY}`);
    headers.set("Content-Type", "application/json");
    headers.set("Accept", request.headers.get("Accept") || "text/event-stream");
    headers.set("User-Agent", "GulAssistant-relay/1.0");

    let body;
    if (request.method !== "GET" && request.method !== "HEAD") {
      body = await request.text();
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort("timeout"), 120000);

    let upstream;
    try {
      upstream = await fetch(`${upstreamOrigin(env)}/${path}`, {
        method: request.method,
        headers,
        body,
        signal: controller.signal,
      });
    } catch (error) {
      clearTimeout(timer);
      return json({ error: "upstream_unreachable", hint: String(error) }, 502);
    }
    clearTimeout(timer);

    const responseHeaders = new Headers(upstream.headers);
    // Cloudflare сам разжимает тело; исходные заголовки кодирования ломают стрим.
    for (const name of ["content-encoding", "content-length", "transfer-encoding", "connection"]) {
      responseHeaders.delete(name);
    }
    for (const [key, value] of Object.entries(CORS)) {
      responseHeaders.set(key, value);
    }

    return new Response(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  },
};
