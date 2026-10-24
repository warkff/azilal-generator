// Azilal wake service — Cloudflare Worker (free tier).
// Secrets to set in the Worker dashboard (Settings -> Variables -> Secrets):
//   GH_PAT          : a FINE-GRAINED GitHub token with ONLY the "Codespaces" permission (Read and write).
//                     Never put this token in the website code or in the repo.
//   CODESPACES_NAME : legendary-umbrella-xj46wvv5w99399pw (change if your codespace name differs)
// Endpoints:
//   POST /wake   -> starts the codespace (202 / {state, name})
//   GET  /status -> current codespace state (Available / Starting / Shutdown / ...)

const GH_API = "https://api.github.com";
const CORS = {
  "Access-Control-Allow-Origin": "https://azilal-reports.web.app",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}

async function gh(env, path, method = "GET") {
  const r = await fetch(GH_API + path, {
    method,
    headers: {
      Authorization: `Bearer ${env.GH_PAT}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "azilal-wake",
    },
  });
  const body = await r.json().catch(() => ({}));
  return { status: r.status, body };
}

async function findCodespace(env) {
  const name = env.CODESPACES_NAME || "legendary-umbrella-xj46wvv5w99399pw";
  const direct = await gh(env, `/user/codespaces/${encodeURIComponent(name)}`);
  if (direct.status === 200) return direct.body;
  const list = await gh(env, "/user/codespaces");
  const all = list.body.codespaces || [];
  return all.find((c) => c.name && c.name.startsWith("legendary-umbrella")) || null;
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS });
    const url = new URL(request.url);
    try {
      if (url.pathname === "/wake" && request.method === "POST") {
        const cs = await findCodespace(env);
        if (!cs) return json({ error: "codespace not found" }, 404);
        const started = await gh(env, `/user/codespaces/${encodeURIComponent(cs.name)}/start`, "POST");
        if (started.status !== 202 && started.status !== 200) {
          return json({ error: "GitHub start failed", github_status: started.status, detail: started.body.message || null }, 502);
        }
        return json({ state: started.body.state || cs.state, name: cs.name });
      }
      if (url.pathname === "/status" && request.method === "GET") {
        // Diagnostic version: reports GitHub's own answers (never the token)
        const name = env.CODESPACES_NAME || "legendary-umbrella-xj46wvv5w99399pw";
        const direct = await gh(env, `/user/codespaces/${encodeURIComponent(name)}`);
        let listInfo = null;
        if (direct.status !== 200) {
          const list = await gh(env, "/user/codespaces");
          listInfo = { status: list.status, count: (list.body.codespaces || []).length,
                       names: (list.body.codespaces || []).map((c) => c.name),
                       message: list.body.message || null };
        }
        const cs = direct.status === 200 ? direct.body : null;
        return json({ state: cs ? cs.state : "unknown", name: cs ? cs.name : null,
                      diag: { direct_status: direct.status, direct_message: direct.body.message || null, list: listInfo } });
      }
      return new Response("not found", { status: 404, headers: CORS });
    } catch (e) {
      return json({ error: String(e) }, 500);
    }
  },
};
