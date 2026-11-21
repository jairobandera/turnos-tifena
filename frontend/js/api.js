// Utilidades compartidas: API, fechas y UI mínima.
const API = {
  async req(method, url, body) {
    const opts = { method, headers: {}, credentials: "same-origin" };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(url, opts);
    let data = null;
    try { data = await res.json(); } catch (_) { /* sin cuerpo */ }
    if (!res.ok) {
      const err = new Error((data && data.error) || `Error ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return data;
  },
  get(url) { return this.req("GET", url); },
  post(url, body) { return this.req("POST", url, body || {}); },
  put(url, body) { return this.req("PUT", url, body || {}); },
  del(url) { return this.req("DELETE", url); },
};

const D = {
  DAYS: ["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO"],
  SHORT: ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"],
  MONTHS: ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"],
  parse(s) { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); },
  iso(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; },
  add(d, n) { const x = new Date(d); x.setDate(x.getDate() + n); return x; },
  monday(d) { const x = new Date(d); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; },
  wd(d) { return (d.getDay() + 6) % 7; },
  dm(d) { return `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}`; },
  title(s) { const d = this.parse(s); return `${this.DAYS[this.wd(d)]} ${this.dm(d)}`; },
  monthBounds(y, m) { return [new Date(y, m - 1, 1), new Date(y, m, 0)]; },
  range(a, b) { const out = []; for (let d = new Date(a); d <= b; d = this.add(d, 1)) out.push(this.iso(d)); return out; },
  today() { return this.iso(new Date()); },
  /** semanas lun-dom que tocan el mes: [{start, end, days:[iso dentro del mes]}] */
  weeksOfMonth(y, m) {
    const [first, last] = this.monthBounds(y, m);
    const out = [];
    for (let mon = this.monday(first); mon <= last; mon = this.add(mon, 7)) {
      const sun = this.add(mon, 6);
      out.push({ start: mon, end: sun, days: this.range(mon < first ? first : mon, sun > last ? last : sun) });
    }
    return out;
  },
};

const UI = {
  esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); },
  toast(msg, ms = 2600) {
    const t = document.createElement("div");
    t.className = "toast";
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), ms);
  },
  busy(msg) {
    const b = document.createElement("div");
    b.className = "busy";
    b.innerHTML = `<div class="spinner"></div><div>${UI.esc(msg)}</div>`;
    document.body.appendChild(b);
    return () => b.remove();
  },
  /** Hoja inferior (celular) / modal (PC). Devuelve {el, close}. */
  sheet(html, onClose) {
    const ov = document.createElement("div");
    ov.className = "overlay";
    ov.innerHTML = `<div class="sheet" role="dialog" aria-modal="true">${html}</div>`;
    const close = () => { ov.remove(); document.removeEventListener("keydown", onKey); onClose && onClose(); };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    ov.addEventListener("click", (e) => { if (e.target === ov || e.target.closest("[data-close]")) close(); });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(ov);
    return { el: ov.querySelector(".sheet"), close };
  },
  confirm(title, text, okLabel = "Aceptar", danger = false) {
    return new Promise((resolve) => {
      let answered = false;
      const s = UI.sheet(`<h3>${UI.esc(title)}</h3><div class="sub">${text}</div>
        <div class="actions"><button class="btn" data-close>Cancelar</button>
        <button class="btn ${danger ? "danger" : "primary"}" data-ok>${UI.esc(okLabel)}</button></div>`, () => { if (!answered) resolve(false); });
      s.el.querySelector("[data-ok]").onclick = () => { answered = true; s.close(); resolve(true); };
    });
  },
};

const LS = {
  get(k, def = null) { try { const v = localStorage.getItem(k); return v === null ? def : JSON.parse(v); } catch (_) { return def; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* privado */ } },
};
