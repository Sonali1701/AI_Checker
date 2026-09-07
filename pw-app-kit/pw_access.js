/**
 * pw_access.js - PW App Kit v2 client for browser, Node, Vercel, and edge apps.
 *
 * The app ships no provider keys. All paid provider calls go through the PW
 * proxy, which verifies the signed-in PW user, checks the app whitelist, calls
 * the provider with proxy-held keys, and writes trusted raw usage logs.
 *
 * Raw logging model:
 *   - One successful proxy/provider call = one raw row in MongoDB/sheet export.
 *   - For multi-call tasks, create one task_id and pass it to every helper.
 *   - Combine later in Sheets/AppScript by task_id + app + email + model.
 */

export const APP_NAME = "SET-YOUR-APP-NAME";
export const PROXY_BASE_URL = "https://pw-apps-proxy.vercel.app";

const TIMEOUT_MS = 30_000;
const AI_TIMEOUT_MS = 300_000;
const LARGE_REQUEST_CHARS = 3_500_000;

export class PWAccessError extends Error {}

export function newTaskId(prefix = "task") {
  const safe = String(prefix || "task").replace(/[^a-zA-Z0-9_-]/g, "") || "task";
  const c = globalThis.crypto;
  const id = c?.randomUUID
    ? c.randomUUID().replace(/-/g, "")
    : `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  return `${safe}-${id}`;
}

function authHeaders(token) {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
}

async function resolveToken(googleToken) {
  return typeof googleToken === "function" ? await googleToken() : googleToken;
}

const sessionPass = { token: "", expiry: 0 };

async function postOnce(path, token, body, timeoutMs) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(`${PROXY_BASE_URL}${path}`, {
      method: "POST",
      headers: authHeaders(token),
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
  } finally {
    clearTimeout(timer);
  }
}

async function authToken(googleToken, forceNew = false) {
  if (!forceNew && sessionPass.token && Date.now() < sessionPass.expiry - 60_000) {
    return sessionPass.token;
  }

  const google = await resolveToken(googleToken);
  try {
    const r = await postOnce("/api/session", google, {}, TIMEOUT_MS);
    if (r.status === 200) {
      const data = await r.json();
      if (data.session_token) {
        sessionPass.token = data.session_token;
        sessionPass.expiry = Number(data.expires_at_ms) || 0;
        return sessionPass.token;
      }
    }
  } catch {
    // Network blip: fall back to the Google token for this call.
  }
  return google;
}

async function postJSON(path, googleToken, body, timeoutMs) {
  let r = await postOnce(path, await authToken(googleToken), body, timeoutMs);
  if (r.status === 401) {
    r = await postOnce(path, await authToken(googleToken, true), body, timeoutMs);
  }
  return r;
}

export async function checkAllowedStatus(googleToken, app = APP_NAME) {
  if (!googleToken) return "denied";
  try {
    const r = await postJSON("/api/allowlist", googleToken, { app }, TIMEOUT_MS);
    if (r.status === 200) return (await r.json()).allowed ? "allowed" : "denied";
    if (r.status === 403) return "denied";
    return "error";
  } catch {
    return "error";
  }
}

export async function checkAllowed(googleToken, app = APP_NAME) {
  return (await checkAllowedStatus(googleToken, app)) === "allowed";
}

export async function logUsage(
  googleToken,
  { filename, input_unit, count, items, task_id = "", taskId = "", video_duration = "", app = APP_NAME }
) {
  try {
    const r = await postJSON(
      "/api/usage-log",
      googleToken,
      { app, filename, input_unit, count, items, task_id: task_id || taskId, video_duration },
      TIMEOUT_MS,
    );
    return r.status === 200 ? await r.json() : null;
  } catch {
    return null;
  }
}

export class UsageSession {
  constructor(
    googleToken,
    {
      filename = "",
      input_unit = "",
      count = null,
      video_duration = "",
      app = APP_NAME,
      task_id = "",
      taskId = "",
    } = {},
  ) {
    this.token = googleToken;
    this.task_id = task_id || taskId || newTaskId();
    this.filename = filename;
    this.input_unit = input_unit;
    this.count = count;
    this.video_duration = video_duration;
    this.app = app;
  }
  add() {
    return null;
  }
  async flush() {
    return {
      ok: true,
      task_id: this.task_id,
      note: "raw provider calls already logged by proxy",
    };
  }
}

function applyTaskContext(body, session = null, taskIdValue = "") {
  let id = taskIdValue;
  if (session) {
    if (!id) id = session.task_id || "";
    if (body.app === APP_NAME && session.app && session.app !== APP_NAME) body.app = session.app;
    for (const key of ["filename", "input_unit", "video_duration"]) {
      if (!body[key] && session[key]) body[key] = session[key];
    }
    if ((body.count == null || body.count === "") && session.count != null) body.count = session.count;
  }
  if (id) body.task_id = id;
}

async function raiseProxyError(name, r) {
  if (r.status !== 200) {
    throw new PWAccessError(`${name} proxy ${r.status}: ${(await r.text()).slice(0, 300)}`);
  }
}

async function uploadLargeJson(googleToken, app, request, providerName) {
  const requestJson = JSON.stringify(request);
  if (requestJson.length <= LARGE_REQUEST_CHARS) return "";

  const up = await postJSON("/api/gemini/upload-url", googleToken, { app }, TIMEOUT_MS);
  if (up.status !== 200) {
    throw new PWAccessError(`${providerName} upload-url ${up.status}: ${(await up.text()).slice(0, 300)}`);
  }
  const { upload_url } = await up.json();
  const pr = await fetch(upload_url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: requestJson,
  });
  if (pr.status !== 200) {
    throw new PWAccessError(`${providerName} upload ${pr.status}: ${(await pr.text()).slice(0, 300)}`);
  }
  return (await pr.json()).url || "";
}

export async function geminiGenerate(
  googleToken,
  {
    model,
    request,
    filename = "",
    input_unit = "",
    count = null,
    task_id = "",
    taskId = "",
    video_duration = "",
    app = APP_NAME,
    session = null,
  },
) {
  const body = { app, model, request, filename, input_unit, count, video_duration };
  applyTaskContext(body, session, task_id || taskId);
  const blobUrl = await uploadLargeJson(googleToken, body.app, request, "gemini");
  if (blobUrl) {
    delete body.request;
    body.request_blob_url = blobUrl;
  }
  const r = await postJSON("/api/gemini/generate", googleToken, body, AI_TIMEOUT_MS);
  await raiseProxyError("gemini", r);
  return await r.json();
}

export async function mathpixOcr(
  googleToken,
  { request, filename = "", count = 1, task_id = "", taskId = "", video_duration = "", app = APP_NAME, session = null },
) {
  const body = { app, request, filename, count, video_duration };
  applyTaskContext(body, session, task_id || taskId);
  const r = await postJSON("/api/mathpix/ocr", googleToken, body, AI_TIMEOUT_MS);
  await raiseProxyError("mathpix", r);
  return await r.json();
}

export async function sarvamTts(
  googleToken,
  { request, filename = "", count = null, task_id = "", taskId = "", video_duration = "", app = APP_NAME, session = null },
) {
  const body = { app, request, filename, count, video_duration };
  applyTaskContext(body, session, task_id || taskId);
  const r = await postJSON("/api/sarvam/tts", googleToken, body, AI_TIMEOUT_MS);
  await raiseProxyError("sarvam", r);
  return await r.json();
}

export async function elevenLabsTts(
  googleToken,
  {
    voice_id,
    request,
    output_format = "mp3_44100_128",
    filename = "",
    count = null,
    task_id = "",
    taskId = "",
    video_duration = "",
    app = APP_NAME,
    session = null,
  },
) {
  const body = { app, voice_id, request, output_format, filename, count, video_duration };
  applyTaskContext(body, session, task_id || taskId);
  const r = await postJSON("/api/elevenlabs/tts", googleToken, body, AI_TIMEOUT_MS);
  await raiseProxyError("elevenlabs", r);
  return await r.json();
}

export async function claudeGenerate(
  googleToken,
  {
    model,
    request,
    filename = "",
    input_unit = "",
    count = null,
    task_id = "",
    taskId = "",
    video_duration = "",
    app = APP_NAME,
    session = null,
  },
) {
  const body = { app, model, request, filename, input_unit, count, video_duration };
  applyTaskContext(body, session, task_id || taskId);
  const blobUrl = await uploadLargeJson(googleToken, body.app, request, "claude");
  if (blobUrl) {
    delete body.request;
    body.request_blob_url = blobUrl;
  }
  const r = await postJSON("/api/claude/generate", googleToken, body, AI_TIMEOUT_MS);
  await raiseProxyError("claude", r);
  return await r.json();
}

export async function geminiTts(
  googleToken,
  {
    text,
    voice = "Kore",
    model = "gemini-3.1-flash-tts-preview",
    filename = "",
    count = null,
    task_id = "",
    taskId = "",
    video_duration = "",
    app = APP_NAME,
    session = null,
  },
) {
  const body = { app, model, text, voice, filename, count, video_duration };
  applyTaskContext(body, session, task_id || taskId);
  const r = await postJSON("/api/gemini/tts", googleToken, body, AI_TIMEOUT_MS);
  await raiseProxyError("gemini tts", r);
  return await r.json();
}

export async function geminiImage(
  googleToken,
  {
    prompt,
    model = "gemini-3.1-flash-image",
    filename = "",
    count = 1,
    task_id = "",
    taskId = "",
    video_duration = "",
    app = APP_NAME,
    session = null,
  },
) {
  const body = { app, model, prompt, filename, count, video_duration };
  applyTaskContext(body, session, task_id || taskId);
  const r = await postJSON("/api/gemini/image", googleToken, body, AI_TIMEOUT_MS);
  await raiseProxyError("gemini image", r);
  return await r.json();
}
