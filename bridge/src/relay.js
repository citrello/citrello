// One Relay (Durable Object) per Citrello account.
//
// The Citrello app on the user's computer keeps a WebSocket open to it. MCP requests from ChatGPT or
// claude.ai arrive as HTTP calls, are passed down that WebSocket, and the app's reply is returned.
// The WebSocket uses the hibernation API, so an idle connection costs nothing.
import { DurableObject } from "cloudflare:workers";

const PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"];
const RPC_TIMEOUT_MS = 115_000;

const DEFAULT_INSTRUCTIONS =
  "Citrello is the user's reference manager. Search the library before citing, add missing references " +
  "by DOI, and cite with [@Key] markers.";

const OFFLINE_TEXT =
  "Citrello isn't open on the user's computer, so the library can't be reached right now. " +
  "Ask the user to open Citrello (the app, or citrello.app/app in a browser tab), signed in as {who}, " +
  "and try again.";

export class Relay extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.pending = new Map(); // request id -> {resolve, timer}
    // Answered by Cloudflare without waking the object.
    ctx.setWebSocketAutoResponse(new WebSocketRequestResponsePair("ping", "pong"));
  }

  async fetch(request) {
    const url = new URL(request.url);
    if (url.pathname === "/connect") return this.connect(request);
    if (url.pathname === "/rpc") {
      const { msg, email } = await request.json();
      return Response.json({ reply: await this.rpc(msg, email) });
    }
    if (url.pathname === "/status") return Response.json(this.status());
    if (url.pathname === "/disconnect") {
      const { deviceId } = await request.json();
      for (const ws of this.ctx.getWebSockets()) {
        const a = ws.deserializeAttachment() || {};
        if (!deviceId || a.deviceId === deviceId) ws.close(4001, "signed out");
      }
      if (!deviceId) await this.ctx.storage.deleteAll();
      return Response.json({ ok: true });
    }
    return new Response("not found", { status: 404 });
  }

  connect(request) {
    if (request.headers.get("Upgrade") !== "websocket") return new Response("expected websocket", { status: 426 });
    const deviceId = request.headers.get("X-Device-Id");
    const name = request.headers.get("X-Device-Name") || "";
    // A device that reconnects replaces its old connection.
    for (const ws of this.ctx.getWebSockets()) {
      if ((ws.deserializeAttachment() || {}).deviceId === deviceId) ws.close(4000, "replaced");
    }
    const pair = new WebSocketPair();
    this.ctx.acceptWebSocket(pair[1]);
    pair[1].serializeAttachment({ deviceId, name, connectedAt: Date.now() });
    // a browser that offered the "citrello" subprotocol (the web app) must get it back, or it drops the connection
    const protocol = request.headers.get("X-Device-Protocol");
    return new Response(null, { status: 101, webSocket: pair[0],
                                headers: protocol ? { "Sec-WebSocket-Protocol": protocol } : {} });
  }

  openSockets() {
    return this.ctx.getWebSockets()
      .filter((ws) => ws.readyState === WebSocket.OPEN)
      .map((ws) => ({ ws, ...(ws.deserializeAttachment() || {}) }))
      .sort((a, b) => b.connectedAt - a.connectedAt);
  }

  status() {
    const devices = this.openSockets().map(({ deviceId, name, connectedAt }) => ({ deviceId, name, connectedAt }));
    return { online: devices.length > 0, devices };
  }

  async webSocketMessage(ws, data) {
    let m;
    try { m = JSON.parse(typeof data === "string" ? data : new TextDecoder().decode(data)); } catch { return; }
    if (m.type === "reply" && this.pending.has(m.id)) {
      const p = this.pending.get(m.id);
      this.pending.delete(m.id);
      clearTimeout(p.timer);
      p.resolve(m.msg);
    } else if (m.type === "hello") {
      // Remember the tool list, so ChatGPT can see the tools even while the app is closed.
      await this.ctx.storage.put("hello", {
        tools: Array.isArray(m.tools) ? m.tools : [],
        instructions: typeof m.instructions === "string" ? m.instructions : DEFAULT_INSTRUCTIONS,
        serverInfo: m.serverInfo || null,
      });
    }
  }

  async webSocketClose(ws, code) {
    try { ws.close(code === 1005 || code === 1006 ? 1000 : code, "closed"); } catch {}
  }

  async webSocketError() {}

  // Send one JSON-RPC request to the most recently connected computer.
  forward(msg) {
    const target = this.openSockets()[0];
    if (!target) return null;
    const id = crypto.randomUUID();
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        resolve({ jsonrpc: "2.0", id: msg.id, error: { code: -32001,
          message: "Citrello on the user's computer didn't answer in time. It may be busy or asleep; try again." } });
      }, RPC_TIMEOUT_MS);
      this.pending.set(id, { resolve, timer });
      try {
        target.ws.send(JSON.stringify({ type: "rpc", id, msg }));
      } catch {
        clearTimeout(timer);
        this.pending.delete(id);
        resolve(null);
      }
    });
  }

  async rpc(msg, email) {
    if (!msg || typeof msg !== "object" || msg.jsonrpc !== "2.0") {
      return { jsonrpc: "2.0", id: null, error: { code: -32600, message: "Invalid request" } };
    }
    const { id, method, params = {} } = msg;
    if (id === undefined || id === null) return null; // notification
    const hello = (await this.ctx.storage.get("hello")) || {};
    const ok = (result) => ({ jsonrpc: "2.0", id, result });

    if (method === "initialize") {
      const asked = params.protocolVersion;
      return ok({
        protocolVersion: PROTOCOL_VERSIONS.includes(asked) ? asked : PROTOCOL_VERSIONS[0],
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: "citrello", title: "Citrello reference manager",
                      version: (hello.serverInfo && hello.serverInfo.version) || "1" },
        instructions: hello.instructions || DEFAULT_INSTRUCTIONS,
      });
    }
    if (method === "ping") return ok({});
    if (method === "tools/list" && !this.openSockets().length) return ok({ tools: hello.tools || [] });
    if (method === "resources/list") return ok({ resources: [] });
    if (method === "prompts/list") return ok({ prompts: [] });

    const reply = await this.forward(msg);
    if (reply) return reply;
    if (method === "tools/list") return ok({ tools: hello.tools || [] });
    if (method === "tools/call") {
      return ok({ content: [{ type: "text", text: OFFLINE_TEXT.replace("{who}", email || "the same email") }], isError: true });
    }
    return { jsonrpc: "2.0", id, error: { code: -32601, message: `Method not found: ${method}` } };
  }
}
