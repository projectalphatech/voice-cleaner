// Voice cleaner front end: serves the page and proxies job calls to the VPS with the secret token.
const API = /^\/api\/jobs(\/[0-9a-f]{32}(\/result)?)?$/;

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (url.pathname === "/") {
      return new Response(PAGE, { headers: { "content-type": "text/html;charset=utf-8" } });
    }
    if (!API.test(url.pathname)) return new Response("Not found", { status: 404 });
    if (req.method !== (url.pathname === "/api/jobs" ? "POST" : "GET")) {
      return new Response("Method not allowed", { status: 405 });
    }
    const headers = { authorization: `Bearer ${env.VPS_TOKEN}` };
    if (req.headers.has("content-type")) headers["content-type"] = req.headers.get("content-type");
    return fetch(env.VPS_URL.replace(/\/$/, "") + url.pathname.slice(4), {
      method: req.method,
      headers,
      body: req.method === "POST" ? req.body : undefined,
    });
  },
};

const PAGE = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Voice Cleaner</title>
<style>
:root{color-scheme:light dark;--bg:#fafafa;--fg:#161616;--muted:#666;--line:#ddd;--accent:#2557d6}
@media (prefers-color-scheme:dark){:root{--bg:#121212;--fg:#eee;--muted:#999;--line:#333;--accent:#7aa2ff}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
main{max-width:520px;margin:48px auto;padding:0 16px}
h1{font-size:1.6rem;margin:0 0 4px}p{color:var(--muted);margin:0 0 24px}
label{display:block;margin:0 0 14px}select,input[type=file]{display:block;width:100%;margin-top:4px;padding:8px;
border:1px solid var(--line);border-radius:8px;background:transparent;color:inherit;box-sizing:border-box}
.row{display:flex;gap:20px}.row label{display:flex;gap:8px;align-items:center}
button{width:100%;padding:12px;border:0;border-radius:8px;background:var(--accent);color:#fff;font-size:1rem;cursor:pointer}
button:disabled{opacity:.5;cursor:wait}#out{margin-top:20px;min-height:24px}a{color:var(--accent)}
</style></head><body><main>
<h1>Voice Cleaner</h1><p>Removes noise and hum, evens out speakers, and sets broadcast loudness.</p>
<form id="f">
<label>Audio or video file<input type="file" name="file" accept="audio/*,video/*" required></label>
<label>Loudness target<select name="target">
<option value="-16">-16 LUFS: podcast (Apple, Google)</option><option value="-14">-14 LUFS: Spotify, YouTube</option>
<option value="-19">-19 LUFS: mono podcast</option><option value="-23">-23 LUFS: EBU R128 broadcast</option>
<option value="-24">-24 LUFS: ATSC A/85 broadcast</option></select></label>
<label>Output format<select name="fmt"><option>mp3</option><option>m4a</option><option>flac</option><option>wav</option></select></label>
<div class="row"><label><input type="checkbox" name="denoise" checked>Noise reduction</label>
<label><input type="checkbox" name="leveler" checked>Leveler</label></div>
<button>Clean audio</button></form>
<div id="out" role="status" aria-live="polite"></div>
</main><script>
const f = document.getElementById("f"), out = document.getElementById("out"), btn = f.querySelector("button");
f.onsubmit = async (e) => {
  e.preventDefault();
  const fd = new FormData(f);
  fd.set("denoise", f.denoise.checked); fd.set("leveler", f.leveler.checked);
  btn.disabled = true; out.textContent = "Uploading…";
  try {
    const r = await fetch("/api/jobs", { method: "POST", body: fd });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const { id } = await r.json();
    for (;;) {
      await new Promise((ok) => setTimeout(ok, 2000));
      const s = await (await fetch("/api/jobs/" + id)).json();
      if (s.status === "error") throw new Error(s.error);
      if (s.status === "done") {
        out.innerHTML = '<a href="/api/jobs/' + id + '/result">Download cleaned file</a> · ' +
          s.info.lufs + " LUFS, peak " + s.info.true_peak + " dBTP";
        break;
      }
      out.textContent = s.status === "queued" ? "Waiting in queue…" : "Processing…";
    }
  } catch (err) { out.textContent = "Failed: " + err.message; }
  btn.disabled = false;
};
</script></body></html>`;
