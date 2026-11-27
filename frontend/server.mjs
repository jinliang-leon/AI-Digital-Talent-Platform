import { createServer, request as upstreamRequest } from "node:http";
import { readFile } from "node:fs/promises";

const assets = new Map([
  ["/", ["index.html", "text/html; charset=utf-8"]],
  ["/app.js", ["app.js", "application/javascript; charset=utf-8"]],
  ["/styles.css", ["styles.css", "text/css; charset=utf-8"]],
]);
const backend = new URL(process.env.TALENT_API_ORIGIN ?? "http://127.0.0.1:18000");
const port = Number(process.env.TALENT_WEB_PORT ?? 5173);
const host = process.env.TALENT_WEB_HOST ?? "127.0.0.1";

function proxy(req, res) {
  const { connection, ...headers } = req.headers;
  const destination = new URL(backend);
  const original = new URL(req.url, "http://localhost");
  destination.pathname = original.pathname;
  destination.search = original.search;
  const upstream = upstreamRequest(destination, {
    method: req.method, headers: { ...headers, host: backend.host }, timeout: 130_000,
  }, (response) => {
    res.writeHead(response.statusCode, response.headers);
    response.pipe(res);
  });
  upstream.on("timeout", () => upstream.destroy());
  upstream.on("error", () => {
    if (res.headersSent) { res.destroy(); return; }
    res.writeHead(502, { "Content-Type": "application/json; charset=utf-8" });
    res.end(JSON.stringify({ message: "后端服务暂不可用" }));
  });
  res.on("close", () => { if (!res.writableEnded) upstream.destroy(); });
  req.pipe(upstream);
}

createServer(async (req, res) => {
  const path = new URL(req.url, "http://localhost").pathname;
  if (path.startsWith("/api/") || path.startsWith("/health/")) { proxy(req, res); return; }
  const asset = assets.get(path);
  if (!asset || !["GET", "HEAD"].includes(req.method)) { res.writeHead(404).end(); return; }
  try {
    const contents = await readFile(new URL(asset[0], import.meta.url));
    res.writeHead(200, {
      "Content-Type": asset[1], "Content-Length": contents.length, "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
      "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    });
    res.end(req.method === "HEAD" ? undefined : contents);
  } catch { res.writeHead(500).end(); }
}).listen(port, host, () => console.log(`Talent Hub web listening at http://${host}:${port}`));
