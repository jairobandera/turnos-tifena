// Vista pública: los empleados consultan turnos publicados y licencias, sin login.
const S = {
  mode: LS.get("pub.mode", "week"),
  anchor: new Date(),
  who: LS.get("pub.who", null),
  view: LS.get("pub.view", "all"),
  licFilter: "current",
  emps: new Map(),
  staffing: null,
  licenses: [],
  licDays: new Map(),
};

const $ = (s) => document.querySelector(s);

function rangeNow() {
  if (S.mode === "week") {
    const mon = D.monday(S.anchor);
    return [mon, D.add(mon, 6)];
  }
  return D.monthBounds(S.anchor.getFullYear(), S.anchor.getMonth() + 1);
}

function setActive(group, attr, value) {
  group.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset[attr] === value));
}

async function load() {
  const [a, b] = rangeNow();
  $("#range-label").textContent = S.mode === "week"
    ? `${D.dm(a)} al ${D.dm(b)}`
    : `${D.MONTHS[a.getMonth()]} ${a.getFullYear()}`;
  const content = $("#content");
  content.innerHTML = `<div class="empty">Cargando…</div>`;
  let data;
  try {
    data = await API.get(`/api/schedule?start=${D.iso(a)}&end=${D.iso(b)}`);
  } catch (e) {
    content.innerHTML = `<div class="alert error">No se pudieron cargar los turnos: ${UI.esc(e.message)}</div>`;
    return;
  }
  const dates = D.range(a, b);
  const published = new Set(data.published_days);
  const byDay = new Map();
  for (const x of data.assignments) {
    if (!byDay.has(x.date)) byDay.set(x.date, []);
    byDay.get(x.date).push(x);
  }
  const anyPublished = dates.some((d) => published.has(d) && byDay.has(d));
  if (!anyPublished) {
    content.innerHTML = `<div class="card empty">📭 Los turnos de este período todavía no se publicaron.</div>`;
    return;
  }
  const unpublished = new Set(dates.filter((d) => !published.has(d) || !byDay.has(d)));
  const whoId = S.who ? Number(S.who) : null;

  if (S.view === "mine" && whoId && S.emps.has(whoId)) {
    content.innerHTML = `<h3>${UI.esc(S.emps.get(whoId).name)}</h3>` +
      renderMyDays(dates, byDay, whoId, { licDays: S.licDays, unpublished });
    return;
  }
  const ctx = { emps: S.emps, staffing: S.staffing, licDays: S.licDays, editable: false, highlight: whoId };
  content.innerHTML = dates
    .filter((d) => !(S.mode === "month" && unpublished.has(d)))
    .map((d) => renderDay(d, byDay.get(d) || [], { ...ctx, unpublished: unpublished.has(d) }))
    .join("");
  // en la semana actual, llevar al día de hoy
  const todayEl = content.querySelector(`.day[data-date="${D.today()}"]`);
  if (todayEl && S.mode === "week" && !S._scrolled) { S._scrolled = true; todayEl.scrollIntoView({ block: "start" }); window.scrollBy(0, -110); }
}

function renderLicenses() {
  const today = D.today();
  const whoId = S.who ? Number(S.who) : null;
  let rows = S.licenses.slice().sort((a, b) => (a.start < b.start ? 1 : -1));
  if (S.licFilter === "current") rows = rows.filter((l) => l.end >= today).reverse();
  if (!rows.length) {
    $("#lic-table").innerHTML = `<div class="empty">No hay licencias ${S.licFilter === "current" ? "vigentes ni próximas" : "registradas"}.</div>`;
    return;
  }
  const state = (l) => l.start > today ? `<span class="badge draft">Próxima</span>` : l.end < today ? `<span class="badge">Finalizada</span>` : `<span class="badge pub">En curso</span>`;
  $("#lic-table").innerHTML = `<table class="grid"><thead><tr><th>Empleado</th><th>Desde</th><th>Hasta</th><th>Tipo</th><th>Estado</th></tr></thead><tbody>
    ${rows.map((l) => `<tr ${l.employee_id === whoId ? 'style="background:var(--me)"' : ""}><td><b>${UI.esc(l.employee_name)}</b></td>
      <td>${D.dm(D.parse(l.start))}/${l.start.slice(0, 4)}</td><td>${D.dm(D.parse(l.end))}/${l.end.slice(0, 4)}</td>
      <td>${UI.esc(l.kind)}</td><td>${state(l)}</td></tr>`).join("")}</tbody></table>`;
}

function refreshViewToggle() {
  $("#view").hidden = !S.who;
  setActive($("#view"), "view", S.view);
}

async function init() {
  const [emps, settings, licenses] = await Promise.all([API.get("/api/employees"), API.get("/api/settings"), API.get("/api/licenses")]);
  emps.forEach((e) => S.emps.set(e.id, e));
  S.staffing = settings.staffing;
  S.licenses = licenses;
  S.licDays = licenseDaysMap(licenses);
  const who = $("#who");
  who.innerHTML += emps.map((e) => `<option value="${e.id}">${UI.esc(e.name)}</option>`).join("");
  if (S.who && S.emps.has(Number(S.who))) who.value = S.who; else S.who = null;

  setActive($("#mode"), "mode", S.mode);
  refreshViewToggle();

  $("#mode").onclick = (e) => { const b = e.target.closest("button"); if (!b) return; S.mode = b.dataset.mode; LS.set("pub.mode", S.mode); setActive($("#mode"), "mode", S.mode); load(); };
  $("#view").onclick = (e) => { const b = e.target.closest("button"); if (!b) return; S.view = b.dataset.view; LS.set("pub.view", S.view); refreshViewToggle(); load(); };
  $("#prev").onclick = () => { S.anchor = S.mode === "week" ? D.add(S.anchor, -7) : new Date(S.anchor.getFullYear(), S.anchor.getMonth() - 1, 1); load(); };
  $("#next").onclick = () => { S.anchor = S.mode === "week" ? D.add(S.anchor, 7) : new Date(S.anchor.getFullYear(), S.anchor.getMonth() + 1, 1); load(); };
  $("#today").onclick = () => { S.anchor = new Date(); S._scrolled = false; load(); };
  who.onchange = () => { S.who = who.value || null; LS.set("pub.who", S.who); if (!S.who) S.view = "all"; refreshViewToggle(); load(); renderLicenses(); };
  $("#tabs").onclick = (e) => {
    const b = e.target.closest("button"); if (!b) return;
    setActive($("#tabs"), "tab", b.dataset.tab);
    $("#tab-turnos").hidden = b.dataset.tab !== "turnos";
    $("#tab-licencias").hidden = b.dataset.tab !== "licencias";
  };
  $("#lic-filter").onclick = (e) => { const b = e.target.closest("button"); if (!b) return; S.licFilter = b.dataset.f; setActive($("#lic-filter"), "f", S.licFilter); renderLicenses(); };
  renderLicenses();
  load();
}

init().catch((e) => { document.querySelector("#content").innerHTML = `<div class="alert error">${UI.esc(e.message)}</div>`; });
