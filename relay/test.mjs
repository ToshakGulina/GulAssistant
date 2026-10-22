/**
 * Локальные тесты релея — без Cloudflare и без настоящего Groq.
 *
 *   node relay/test.mjs
 *
 * Поднимает фальшивый upstream на 127.0.0.1 и гоняет через worker.fetch()
 * реальные Request/Response: авторизацию, стриминг и отказы.
 */

import http from "node:http";
import assert from "node:assert/strict";
import worker from "./groq-relay.js";

const GROQ_KEY = "gsk_test_key";
const RELAY_TOKEN = "relay_secret";

let seenAuth = "";
let receivedBody = "";

const upstream = http.createServer((req, res) => {
  seenAuth = req.headers.authorization || "";
  let body = "";
  req.on("data", (chunk) => (body += chunk));
  req.on("end", () => {
    receivedBody = body;
    if (req.url === "/openai/v1/audio/transcriptions") {
      const boundary = "gulboundary";
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({
        text: "проверка",
        saw_content_type: (req.headers["content-type"] || "").includes("multipart/form-data"),
      }));
      return;
    }
    if (req.url === "/openai/v1/models") {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ object: "list", data: [{ id: "openai/gpt-oss-20b" }] }));
      return;
    }
    // Стриминг, как у Groq.
    res.writeHead(200, { "content-type": "text/event-stream" });
    res.write('data: {"choices":[{"delta":{"content":"При"}}]}\n\n');
    res.write('data: {"choices":[{"delta":{"content":"вет"}}]}\n\n');
    res.end("data: [DONE]\n\n");
  });
});

await new Promise((resolve) => upstream.listen(0, "127.0.0.1", resolve));
const port = upstream.address().port;

const env = {
  GROQ_API_KEY: GROQ_KEY,
  RELAY_TOKEN,
  GROQ_ORIGIN: `http://127.0.0.1:${port}`,
};

const call = (path, options = {}) =>
  worker.fetch(new Request(`https://relay.example${path}`, options), env);

let passed = 0;

function ok(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => {
      passed += 1;
      console.log(`  ✓ ${name}`);
    })
    .catch((error) => {
      console.error(`  ✗ ${name}`);
      console.error(error);
      process.exitCode = 1;
    });
}

console.log("Релей: локальные тесты");

await ok("health отвечает и не раскрывает ключ", async () => {
  const response = await call("/health");
  assert.equal(response.status, 200);
  const data = await response.json();
  assert.equal(data.ok, true);
  assert.equal(data.key_configured, true);
  assert.equal(data.token_required, true);
  assert.equal(JSON.stringify(data).includes(GROQ_KEY), false);
});

await ok("без токена — 403", async () => {
  const response = await call("/openai/v1/chat/completions", {
    method: "POST",
    body: JSON.stringify({ model: "openai/gpt-oss-20b" }),
  });
  assert.equal(response.status, 403);
});

await ok("с неверным токеном — 403", async () => {
  const response = await call("/openai/v1/chat/completions", {
    method: "POST",
    headers: { Authorization: "Bearer wrong" },
    body: "{}",
  });
  assert.equal(response.status, 403);
});

await ok("стриминг проходит через релей", async () => {
  const response = await call("/openai/v1/chat/completions", {
    method: "POST",
    headers: { Authorization: `Bearer ${RELAY_TOKEN}`, "content-type": "application/json" },
    body: JSON.stringify({ model: "openai/gpt-oss-20b", stream: true }),
  });
  assert.equal(response.status, 200);
  const text = await response.text();
  assert.match(text, /При/);
  assert.match(text, /вет/);
  assert.match(text, /\[DONE\]/);
});

await ok("ключ Groq подставляется, ключ клиента не утекает", async () => {
  assert.equal(seenAuth, `Bearer ${GROQ_KEY}`);
  assert.equal(receivedBody.includes(RELAY_TOKEN), false);
});

await ok("тело запроса доходит без изменений", async () => {
  assert.equal(receivedBody, JSON.stringify({ model: "openai/gpt-oss-20b", stream: true }));
});

await ok("короткий путь /models тоже работает", async () => {
  const response = await call("/openai/v1/models", {
    headers: { Authorization: `Bearer ${RELAY_TOKEN}` },
  });
  assert.equal(response.status, 200);
  const data = await response.json();
  assert.equal(data.data[0].id, "openai/gpt-oss-20b");
});

await ok("распознавание речи проходит через релей с multipart", async () => {
  const form = new FormData();
  form.append("model", "whisper-large-v3-turbo");
  form.append("file", new Blob([new Uint8Array([1, 2, 3])], { type: "audio/wav" }), "cmd.wav");
  const response = await worker.fetch(
    new Request("https://relay.example/openai/v1/audio/transcriptions", {
      method: "POST",
      headers: { Authorization: `Bearer ${RELAY_TOKEN}` },
      body: form,
    }),
    env,
  );
  assert.equal(response.status, 200);
  const data = await response.json();
  assert.equal(data.text, "проверка");
  // Content-Type с boundary должен дойти до Groq без подмены.
  assert.equal(data.saw_content_type, true);
});

await ok("посторонний путь — 404", async () => {
  const response = await call("/openai/v1/completions", {
    method: "POST",
    headers: { Authorization: `Bearer ${RELAY_TOKEN}` },
    body: "{}",
  });
  assert.equal(response.status, 404);
});

await ok("GET разрешён для models (стрим не нужен)", async () => {
  const response = await call("/openai/v1/models", {
    headers: { Authorization: `Bearer ${RELAY_TOKEN}` },
  });
  assert.equal(response.status, 200);
});

await ok("без GROQ_API_KEY /health показывает key_configured=false", async () => {
  const response = await worker.fetch(
    new Request("https://relay.example/health"),
    { RELAY_TOKEN },
  );
  const data = await response.json();
  assert.equal(data.key_configured, false);
});

upstream.close();
console.log(`\nГотово: ${passed} проверок прошло.`);
