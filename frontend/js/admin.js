// Panel del encargado: armar, editar, validar, guardar, publicar y exportar turnos.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const SHIFT_START = { T3: -2, T1: 6, T2: 14 };

const today = new Date();
const A = {
  year: today.getFullYear(),
  month: today.getMonth() + 1,
  assignments: [],
  history: [],
  nextDays: [],
  status: null,
  dirty: false,
  undo: [],
  analysis: null,
  week: null,           // índice de semana o "all"
  emps: new Map(),
  empList: [],
  settings: null,
  licenses: [],
  licDays: new Map(),
  suggestedDesde: null,
  highlight: null,
  sortables: [],
  me: null,             // usuario logueado {user, employee_id}
  analyzeTimer: null,
  licEditing: null,
};

// ============ arranque / sesión ============

async function boot() {
  const me = await API.get("/api/me");
  if (!me.user) return showLogin();
  startApp(me);
}

function showLogin() {
  $("#login").hidden = false;
  $("#app").hidden = true;
  $("#logout").hidden = true;
  $("#login-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      const me = await API.post("/api/login", { username: f.get("username"), password: f.get("password") });
      $("#login").hidden = true;
      startApp({ user: me.username, must_change: me.must_change, employee_id: me.employee_id });
    } catch (err) {
      $("#login-err").hidden = false;
      $("#login-err").textContent = err.message;
    }
  };
}

async function startApp(me) {
  A.me = me;
  $("#app").hidden = false;
  $("#logout").hidden = false;
  $("#user").textContent = me.user;
  $("#logout").onclick = async () => {
    if (A.dirty && !(await UI.confirm("Cambios sin guardar", "Si salís ahora se pierden los cambios.", "Salir igual", true))) return;
    await API.post("/api/logout");
    A.dirty = false;
    location.reload();
  };
  if (me.must_change) {
    $("#pwd-alert").innerHTML = `<div class="alert error">Estás usando la contraseña inicial. Cambiala abajo.</div>`;
    UI.toast("Cambiá la contraseña inicial en Configuración", 4500);
  }
  bindTabs();
  bindTurnos();
  await loadMeta();
  await loadMonth();
}

function bindTabs() {
  $("#tabs").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    $$("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
    for (const t of ["turnos", "reporte", "licencias", "empleados", "config"]) $(`#tab-${t}`).hidden = t !== b.dataset.tab;
    if (b.dataset.tab === "reporte") renderReport();
    if (b.dataset.tab === "licencias") renderLicenses();
    if (b.dataset.tab === "empleados") renderEmployees();
    if (b.dataset.tab === "config") renderConfig();
  };
}

async function loadMeta() {
  const [emps, settings, licenses] = await Promise.all([API.get("/api/admin/employees"), API.get("/api/settings"), API.get("/api/admin/licenses")]);
  A.empList = emps;
  A.emps = new Map(emps.map((e) => [e.id, e]));
  A.settings = settings;
  A.licenses = licenses;
  A.licDays = licenseDaysMap(licenses);
  const opts = emps.filter((e) => e.active).map((e) => `<option value="${e.id}">${UI.esc(e.name)}</option>`).join("");
  $("#hl").innerHTML = `<option value="">Resaltar empleado…</option>${opts}`;
  if (A.highlight) $("#hl").value = A.highlight;
  $("#lic-form [name=employee_id]").innerHTML = `<option value="">Elegí…</option>${emps.map((e) => `<option value="${e.id}">${UI.esc(e.name)}</option>`).join("")}`;
  $("#lic-emp").innerHTML = `<option value="">Todos los empleados</option>${opts}`;
}

// ============ turnos: carga y estado ============

async function loadMonth() {
  const stop = UI.busy("Cargando…");
  try {
    const r = await API.get(`/api/admin/month?year=${A.year}&month=${A.month}`);
    A.assignments = r.assignments;
    A.history = r.history;
    A.nextDays = r.next_days;
    A.status = r.status;
    A.analysis = r.analysis;
    A.suggestedDesde = r.suggested_desde;
    A.dirty = false;
    A.undo = [];
    A.lastGen = null;
    const weeks = D.weeksOfMonth(A.year, A.month);
    const t = D.today();
    const idx = weeks.findIndex((w) => w.days.includes(t));
    A.week = idx >= 0 ? idx : 0;
  } finally {
    stop();
  }
  renderTurnos();
}

function markDirty() {
  A.dirty = true;
  scheduleAnalyze();
  renderTurnos();
}

function pushUndo() {
  A.undo.push(JSON.stringify(A.assignments));
  if (A.undo.length > 60) A.undo.shift();
}

function scheduleAnalyze() {
  clearTimeout(A.analyzeTimer);
  A.analyzeTimer = setTimeout(async () => {
    try {
      A.analysis = await API.post("/api/admin/analyze", { year: A.year, month: A.month, assignments: A.assignments });
      renderTurnos(true);
    } catch (e) { /* se reintenta en el próximo cambio */ }
  }, 350);
}

window.addEventListener("beforeunload", (e) => { if (A.dirty) { e.preventDefault(); e.returnValue = ""; } });

// ============ turnos: render ============

function renderTurnos(keepScroll) {
  const y = window.scrollY;
  $("#m-label").textContent = `${D.MONTHS[A.month - 1]} ${A.year}`;
  const st = $("#m-status");
  st.className = "badge " + (A.status === "publicado" ? "pub" : A.status === "borrador" ? "draft" : "");
  st.textContent = A.status === "publicado" ? "Publicado" : A.status === "borrador" ? "Borrador (no lo ven los empleados)" : "Sin armar";
  $("#m-dirty").hidden = !A.dirty;
  $("#b-undo").disabled = !A.undo.length;
  $("#b-publish").textContent = A.status === "publicado" ? "🙈 Despublicar" : "📢 Publicar";

  // avisos generales
  const issues = A.analysis ? A.analysis.issues : [];
  const general = issues.filter((i) => !i.date && i.level !== "info");
  const empty = issues.filter((i) => i.msg === "Día sin armar").length;
  let alerts = "";
  if (A.lastGen) {
    const g = A.lastGen;
    alerts += `<div class="alert ok">✅ Turnos generados del ${D.dm(D.parse(g.generated_days[0]))} al ${D.dm(D.parse(g.generated_days[g.generated_days.length - 1]))}. Revisalos y tocá <b>Guardar</b>.</div>`;
    if (g.faltantes.length) alerts += `<div class="alert error">No alcanzó el personal para: ${g.faltantes.map(UI.esc).join(" · ")}</div>`;
    if (!g.estricto) alerts += `<div class="alert">No se pudo respetar 100% el 4-4-4 de cajeros; mirá los avisos de cada día.</div>`;
  }
  if (empty) alerts += `<div class="alert info">${empty} día(s) del mes sin armar. Usá <b>Generar</b> para completarlos.</div>`;
  if (general.length) {
    alerts += `<details class="alert"><summary><b>${general.length} aviso(s) del mes</b> (libres, horas de 4)</summary><ul style="margin:6px 0 0;padding-left:18px">${general.map((i) => `<li>${UI.esc(i.msg)}</li>`).join("")}</ul></details>`;
  }
  $("#m-alerts").innerHTML = alerts;

  // pestañas de semana
  const weeks = D.weeksOfMonth(A.year, A.month);
  $("#weeks").innerHTML = weeks.map((w, i) => `<button data-w="${i}" class="${A.week === i ? "active" : ""}">${D.dm(D.parse(w.days[0]))} – ${D.dm(D.parse(w.days[w.days.length - 1]))}</button>`).join("")
    + `<button data-w="all" class="${A.week === "all" ? "active" : ""}">Mes completo</button>`;
  const days = A.week === "all" ? weeks.flatMap((w) => w.days) : weeks[A.week].days;

  const byDay = new Map();
  A.assignments.forEach((a, i) => {
    a._i = i;
    if (!byDay.has(a.date)) byDay.set(a.date, []);
    byDay.get(a.date).push(a);
  });
  const ctx = { emps: A.emps, staffing: A.settings.staffing, licDays: A.licDays, editable: true, highlight: A.highlight, issues,
    canTask: A.me && canTask(A.me.employee_id) };
  $("#days").innerHTML = days.map((d) => renderDay(d, byDay.get(d) || [], ctx)).join("");
  initSortables();
  if (keepScroll) window.scrollTo(0, y);
}

function initSortables() {
  A.sortables.forEach((s) => s.destroy());
  A.sortables = [];
  if (typeof Sortable === "undefined") return;
  const common = {
    group: "turnos",
    animation: 120,
    delay: 180,
    delayOnTouchOnly: true,
    touchStartThreshold: 4,
    filter: ".x",
    preventOnFilter: false,
    onEnd: onDrop,
  };
  $$("#days .slot").forEach((el) => A.sortables.push(Sortable.create(el, common)));
  $$("#days .libre-list").forEach((el) => A.sortables.push(Sortable.create(el, { ...common, sort: false })));
}

function onDrop(evt) {
  const { item, from, to } = evt;
  if (from === to && evt.oldIndex === evt.newIndex) return;
  const eid = Number(item.dataset.emp);
  const idx = item.dataset.idx !== undefined ? Number(item.dataset.idx) : null;
  pushUndo();
  if (to.classList.contains("slot")) {
    const { date, shift, pos } = to.dataset;
    let moved;
    if (idx !== null) {
      moved = A.assignments[idx];
      Object.assign(moved, { date, shift, position: pos });
    } else {
      moved = { date, shift, position: pos, employee_id: eid, ord: 0, locked: false };
      A.assignments.push(moved);
    }
    [...to.children].forEach((li, i) => {
      if (li === item) moved.ord = i;
      else if (li.dataset.idx !== undefined) A.assignments[Number(li.dataset.idx)].ord = i;
    });
  } else if (to.classList.contains("libre-list") && idx !== null) {
    A.assignments.splice(idx, 1);
  } else {
    A.undo.pop();
    renderTurnos(true);
    return;
  }
  markDirty();
}

// ============ turnos: acciones con toque ============

function allKnown() {
  return [...A.history, ...A.assignments, ...A.nextDays];
}

/** Descanso con el turno anterior/siguiente de esa persona, para avisar en el selector. */
function restHint(eid, date, shift) {
  const s0 = D.parse(date).getTime() / 3600000 + SHIFT_START[shift];
  const e0 = s0 + 8;
  let worst = null;
  for (const a of allKnown()) {
    if (a.employee_id !== eid || (a.date === date && a.shift === shift)) continue;
    const s = D.parse(a.date).getTime() / 3600000 + SHIFT_START[a.shift];
    const e = s + 8;
    const rest = s >= e0 ? s - e0 : e <= s0 ? s0 - e : -1;
    if (rest > 16) continue;
    if (rest <= 0) return "⛔ choca con otro turno";
    if (rest <= 8) worst = "⚠️ hora de 4";
  }
  return worst;
}

function pickEmployee(date, shift, pos, replaceIdx = null) {
  const dayAs = A.assignments.filter((a) => a.date === date);
  const working = new Map(dayAs.map((a) => [a.employee_id, a]));
  const groups = { principal: [], suplente: [], otro: [], trabajando: [], licencia: [] };
  for (const e of A.empList.filter((x) => x.active)) {
    if (A.licDays.get(e.id)?.has(date)) groups.licencia.push(e);
    else if (working.has(e.id)) groups.trabajando.push(e);
    else if (pos === TASK) { if (canTask(e.id)) groups.principal.push(e); }
    else groups[e.positions[pos] || "otro"].push(e);
  }
  const opt = (e, extra = "", disabled = false) => {
    const hint = restHint(e.id, date, shift);
    return `<button class="opt" data-emp="${e.id}" ${disabled ? "disabled" : ""}><span>${UI.esc(e.name)}</span><small>${[extra, hint].filter(Boolean).join(" · ")}</small></button>`;
  };
  const sec = (title, list, fn) => (list.length ? `<div class="group-title">${title}</div>${list.map(fn).join("")}` : "");
  const sname = SHIFT_BY_KEY[shift].name;
  const html = `<h3>${replaceIdx !== null ? "Reemplazar" : "Agregar"}: ${posFullName(shift, pos)}</h3>
    <div class="sub">${D.title(date)} · ${sname} (${SHIFT_BY_KEY[shift].hours})</div>
    <div class="opts">
      ${sec(pos === TASK ? "Encargado" : "Libres · su puesto", groups.principal, (e) => opt(e))}
      ${sec("Libres · suplentes", groups.suplente, (e) => opt(e, "suplente"))}
      ${sec("Libres · otro puesto (caso especial)", groups.otro, (e) => opt(e, "no es su puesto"))}
      ${sec("Ya trabajan ese día", pos === TASK ? groups.trabajando.filter((e) => canTask(e.id)) : groups.trabajando, (e) => opt(e, SHIFT_BY_KEY[working.get(e.id).shift].name))}
      ${sec("De licencia", groups.licencia, (e) => opt(e, "licencia", true))}
    </div>
    <div class="actions"><button class="btn" data-close>Cancelar</button></div>`;
  const s = UI.sheet(html);
  s.el.querySelector(".opts").onclick = (e) => {
    const b = e.target.closest(".opt");
    if (!b || b.disabled) return;
    const eid = Number(b.dataset.emp);
    s.close();
    const apply = (note) => {
      pushUndo();
      if (replaceIdx !== null) A.assignments[replaceIdx].employee_id = eid;
      else {
        const ord = A.assignments.filter((a) => a.date === date && a.shift === shift && a.position === pos).length;
        A.assignments.push({ date, shift, position: pos, employee_id: eid, ord, locked: false, note: note || "" });
      }
      markDirty();
    };
    if (pos === TASK && replaceIdx === null) askTask("", apply);
    else apply();
  };
}

/** "Otras tareas": sólo el encargado logueado, para sí mismo. */
function canTask(eid) {
  const e = A.emps.get(eid);
  return !!(A.me && A.me.employee_id === eid && e && e.positions[TASK]);
}

/** Pide la descripción de la tarea (ej. Corte de pasto). */
function askTask(current, done) {
  const presets = ["Corte de pasto", "Limpieza", "Mantenimiento", "Depósito", "Trámites"];
  const s = UI.sheet(`<h3>Otras tareas</h3>
    <div class="sub">Trabaja ese turno pero no ocupa puesto de cajero, pistero ni spot.</div>
    <form id="task-form" class="opts">
      <label class="field">¿Qué tarea?<input name="note" maxlength="60" required value="${UI.esc(current || "")}" placeholder="Ej: Corte de pasto" list="task-presets"></label>
      <datalist id="task-presets">${presets.map((p) => `<option value="${p}">`).join("")}</datalist>
      <div style="display:flex;flex-wrap:wrap;gap:6px">${presets.map((p) => `<button type="button" class="btn small" data-preset="${p}">${p}</button>`).join("")}</div>
      <div class="actions"><button type="button" class="btn" data-close>Cancelar</button><button class="btn primary">Aceptar</button></div>
    </form>`);
  const f = $("#task-form", s.el);
  f.onclick = (e) => { const b = e.target.closest("[data-preset]"); if (b) f.elements.note.value = b.dataset.preset; };
  f.onsubmit = (e) => { e.preventDefault(); const v = f.elements.note.value.trim(); s.close(); if (v) done(v); };
  setTimeout(() => f.elements.note.focus(), 50);
}

function chipMenu(idx) {
  const a = A.assignments[idx];
  const emp = A.emps.get(a.employee_id);
  const staffing = A.settings.staffing;
  const moves = [];
  for (const s of SHIFTS) {
    for (const p of POSITIONS) {
      if ((staffing[s.key]?.[p] || 0) > 0 && !(s.key === a.shift && p === a.position)) moves.push([s.key, p]);
    }
    if (canTask(a.employee_id) && !(s.key === a.shift && a.position === TASK)) moves.push([s.key, TASK]);
  }
  const html = `<h3>${UI.esc(emp ? emp.name : "")}</h3>
    <div class="sub">${D.title(a.date)} · ${SHIFT_BY_KEY[a.shift].name} · ${posFullName(a.shift, a.position)}</div>
    <div class="opts">
      <button class="opt" data-act="lock"><span>${a.locked ? "🔓 Quitar candado" : "🔒 Fijar (el generador no lo toca)"}</span></button>
      <button class="opt" data-act="replace"><span>🔁 Reemplazar por otra persona</span></button>
      ${a.position === TASK && canTask(a.employee_id) ? `<button class="opt" data-act="note"><span>✏️ Cambiar la tarea</span><small>${UI.esc(a.note || "")}</small></button>` : ""}
      <div class="group-title">Mover dentro del día a…</div>
      ${moves.map(([s, p]) => `<button class="opt" data-act="move" data-s="${s}" data-p="${p}"><span>${SHIFT_BY_KEY[s].name} · ${posFullName(s, p)}</span><small>${restHint(a.employee_id, a.date, s) || ""}</small></button>`).join("")}
      <button class="opt" data-act="remove" style="color:var(--err)"><span>✕ Quitar (queda libre)</span></button>
    </div>
    <div class="actions"><button class="btn" data-close>Cerrar</button></div>`;
  const s = UI.sheet(html);
  s.el.querySelector(".opts").onclick = (e) => {
    const b = e.target.closest(".opt");
    if (!b) return;
    const act = b.dataset.act;
    s.close();
    if (act === "replace") return pickEmployee(a.date, a.shift, a.position, idx);
    if (act === "note") return askTask(a.note, (note) => { pushUndo(); a.note = note; markDirty(); });
    if (act === "move" && b.dataset.p === TASK) {
      return askTask(a.note, (note) => {
        pushUndo();
        Object.assign(a, { shift: b.dataset.s, position: TASK, note, ord: 0 });
        markDirty();
      });
    }
    pushUndo();
    if (act === "lock") a.locked = !a.locked;
    if (act === "remove") A.assignments.splice(idx, 1);
    if (act === "move") {
      a.shift = b.dataset.s;
      a.position = b.dataset.p;
      a.ord = A.assignments.filter((x) => x !== a && x.date === a.date && x.shift === a.shift && x.position === a.position).length;
    }
    markDirty();
  };
}

function bindTurnos() {
  $("#days").addEventListener("click", (e) => {
    const rm = e.target.closest("[data-remove]");
    if (rm) {
      pushUndo();
      A.assignments.splice(Number(rm.dataset.remove), 1);
      return markDirty();
    }
    const add = e.target.closest("[data-add]");
    if (add) {
      const [date, shift, pos] = add.dataset.add.split("|");
      return pickEmployee(date, shift, pos);
    }
    const chip = e.target.closest(".chip[data-idx]");
    if (chip) return chipMenu(Number(chip.dataset.idx));
  });
  $("#weeks").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    A.week = b.dataset.w === "all" ? "all" : Number(b.dataset.w);
    renderTurnos();
  };
  $("#hl").onchange = () => { A.highlight = $("#hl").value ? Number($("#hl").value) : null; renderTurnos(true); };
  const changeMonth = async (delta) => {
    if (A.dirty && !(await UI.confirm("Cambios sin guardar", "Si cambiás de mes se pierden los cambios no guardados.", "Cambiar igual", true))) return;
    const d = new Date(A.year, A.month - 1 + delta, 1);
    A.year = d.getFullYear();
    A.month = d.getMonth() + 1;
    loadMonth();
  };
  $("#m-prev").onclick = () => changeMonth(-1);
  $("#m-next").onclick = () => changeMonth(1);
  $("#b-undo").onclick = () => {
    if (!A.undo.length) return;
    A.assignments = JSON.parse(A.undo.pop());
    markDirty();
  };
  $("#b-save").onclick = saveMonth;
  $("#b-publish").onclick = togglePublish;
  $("#b-generate").onclick = generateDialog;
  $("#b-export").onclick = exportDialog;
}

async function saveMonth() {
  const stop = UI.busy("Guardando…");
  try {
    const clean = A.assignments.map(({ _i, ...a }) => a);
    const r = await API.put("/api/admin/month", { year: A.year, month: A.month, assignments: clean });
    A.status = r.status;
    A.analysis = r.analysis;
    A.dirty = false;
    A.lastGen = null;
    UI.toast("Guardado ✔");
    renderTurnos(true);
    return true;
  } catch (e) {
    UI.toast("No se pudo guardar: " + e.message, 4000);
    return false;
  } finally {
    stop();
  }
}

async function togglePublish() {
  const publish = A.status !== "publicado";
  if (publish && A.analysis && A.analysis.issues.some((i) => i.level === "error")) {
    const ok = await UI.confirm("Hay errores", "Hay avisos en rojo (licencias, turnos pegados o personas repetidas). ¿Publicar igual?", "Publicar igual", true);
    if (!ok) return;
  }
  if (A.dirty && !(await saveMonth())) return;
  const r = await API.post("/api/admin/month/status", { year: A.year, month: A.month, status: publish ? "publicado" : "borrador" });
  A.status = r.status;
  UI.toast(publish ? "Publicado: los empleados ya lo ven" : "Pasó a borrador: los empleados no lo ven");
  renderTurnos(true);
}

function generateDialog() {
  const [first, last] = D.monthBounds(A.year, A.month);
  const desde = A.suggestedDesde || D.iso(first);
  const hasData = A.assignments.length > 0;
  const html = `<h3>Generar turnos de ${D.MONTHS[A.month - 1]}</h3>
    <div class="sub">Se toman como punto de partida los turnos ya armados (rotación, bloques de 4, horas de 4 y libres) y se arma de ahí en adelante.</div>
    <form id="gen-form" class="opts">
      <label class="field">Desde el día<input type="date" name="desde" value="${desde}" min="${D.iso(first)}" max="${D.iso(last)}" required></label>
      <label class="opt"><span><input type="radio" name="mode" value="completar" checked> Completar sólo los días vacíos<br><small>Lo que ya está armado no se toca.</small></span></label>
      <label class="opt"><span><input type="radio" name="mode" value="rehacer"> Rehacer desde esa fecha<br><small>Borra y vuelve a armar, salvo lo que tenga 🔒.</small></span></label>
      ${hasData ? "" : `<div class="alert info">El mes está vacío: se arma completo.</div>`}
      <div class="actions"><button type="button" class="btn" data-close>Cancelar</button><button class="btn accent">⚙️ Generar</button></div>
    </form>`;
  const s = UI.sheet(html);
  $("#gen-form", s.el).onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    s.close();
    const secs = A.settings.tiempo_generador || 25;
    const stop = UI.busy(`Armando los turnos… (puede tardar hasta ${secs} segundos)`);
    try {
      const clean = A.assignments.map(({ _i, ...a }) => a);
      const r = await API.post("/api/admin/generate", { year: A.year, month: A.month, desde: f.get("desde"), mode: f.get("mode"), assignments: clean });
      pushUndo();
      A.assignments = r.assignments;
      A.analysis = r.analysis;
      A.lastGen = r;
      A.dirty = true;
      const weeks = D.weeksOfMonth(A.year, A.month);
      const wi = weeks.findIndex((w) => w.days.includes(r.generated_days[0]));
      if (wi >= 0) A.week = wi;
      renderTurnos();
    } catch (err) {
      UI.toast("No se pudo generar: " + err.message, 5000);
    } finally {
      stop();
    }
  };
}

// ============ exportar / compartir ============

function exportDialog() {
  const weeks = D.weeksOfMonth(A.year, A.month);
  const cur = A.week === "all" ? 0 : A.week;
  const [first, last] = D.monthBounds(A.year, A.month);
  const html = `<h3>Exportar turnos</h3>
    <div class="sub">Sale igual que la planilla: Turno 3, Turno 1, Turno 2 y Libres. El reporte no se incluye.</div>
    <form id="exp-form" class="opts">
      <label class="field">¿Qué querés exportar?
        <select name="range">
          ${weeks.map((w, i) => `<option value="${D.iso(w.start)}|${D.iso(w.end)}" ${i === cur ? "selected" : ""}>Semana ${D.dm(w.start)} al ${D.dm(w.end)}</option>`).join("")}
          <option value="${D.iso(first)}|${D.iso(last)}">Mes completo (${D.MONTHS[A.month - 1]})</option>
        </select></label>
      <label class="field">Formato
        <select name="fmt"><option value="pdf">PDF</option><option value="excel">Excel</option></select></label>
      <div class="actions">
        <button type="button" class="btn" data-close>Cancelar</button>
        <button type="submit" class="btn" name="act" value="download">⬇️ Descargar</button>
        <button type="submit" class="btn whatsapp" name="act" value="share">Enviar por WhatsApp</button>
      </div>
    </form>`;
  const s = UI.sheet(html);
  $("#exp-form", s.el).onsubmit = async (e) => {
    e.preventDefault();
    const act = e.submitter ? e.submitter.value : "download";
    const f = new FormData(e.target);
    const [start, end] = f.get("range").split("|");
    const fmt = f.get("fmt");
    s.close();
    if (A.dirty) {
      UI.toast("Guardando cambios antes de exportar…");
      if (!(await saveMonth())) return;
    }
    const stop = UI.busy("Preparando archivo…");
    let blob, name;
    try {
      const res = await fetch(`/api/admin/export/${fmt}?start=${start}&end=${end}`, { credentials: "same-origin" });
      if (!res.ok) throw new Error("No se pudo exportar");
      blob = await res.blob();
      const cd = res.headers.get("Content-Disposition") || "";
      const m = /filename\*=UTF-8''([^;]+)/i.exec(cd) || /filename="?([^";]+)"?/i.exec(cd);
      name = m ? decodeURIComponent(m[1]) : `turnos.${fmt === "pdf" ? "pdf" : "xlsx"}`;
    } catch (err) {
      stop();
      return UI.toast(err.message, 4000);
    }
    stop();
    if (act === "share") {
      const file = new File([blob], name, { type: blob.type });
      if (navigator.canShare && navigator.canShare({ files: [file] })) {
        try {
          await navigator.share({ files: [file], title: name, text: name });
          return;
        } catch (err) {
          if (err.name === "AbortError") return;
        }
      }
      download(blob, name);
      window.open(`https://wa.me/?text=${encodeURIComponent(`${name} (te lo adjunto)`)}`, "_blank");
      UI.toast("Se descargó el archivo: adjuntalo en el chat de WhatsApp que se abrió", 6000);
    } else {
      download(blob, name);
    }
  };
}

function download(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

// ============ reporte ============

function renderReport() {
  const el = $("#report");
  if (!A.analysis) { el.innerHTML = `<div class="empty">Sin datos</div>`; return; }
  const rep = A.analysis.report.filter((r) => A.emps.get(r.employee_id)?.active || r.turnos.T1 + r.turnos.T2 + r.turnos.T3 > 0);
  const weeks = A.analysis.weeks;
  const empty = A.analysis.issues.filter((i) => i.msg === "Día sin armar").length;
  const clsLib = (r) => (r.libres < r.libres_objetivo ? "bad" : r.libres === r.libres_objetivo ? "good" : "muted");
  const clsH4 = (n, max) => (n > max ? "bad" : n === max && n > 0 ? "meh" : "good");
  const head = weeks.map((w) => `<th class="num">${D.dm(D.parse(w.start))}–${D.dm(D.parse(w.end))}<br><small>Libres · H4</small></th>`).join("");
  const rows = rep.map((r) => `<tr>
      <td class="sticky"><b>${UI.esc(r.name)}</b></td>
      <td class="num"><span class="${clsLib(r)}">${r.libres}</span> / ${r.libres_objetivo}</td>
      <td class="num"><span class="${clsH4(r.horas4, r.max_horas4)}">${r.horas4}</span> / ${r.max_horas4}</td>
      <td class="num">${r.licencia || ""}</td>
      <td class="num">${r.turnos.T1}</td><td class="num">${r.turnos.T2}</td><td class="num">${r.turnos.T3}</td>
      ${r.semanas.map((w) => `<td class="num">${w.libres} · <span class="${w.horas4 ? "meh" : ""}">${w.horas4}</span></td>`).join("")}
    </tr>`).join("");
  el.innerHTML = `
    ${A.dirty ? `<div class="alert">Incluye cambios sin guardar.</div>` : ""}
    ${empty ? `<div class="alert">Hay ${empty} día(s) sin armar: cuentan como libres hasta que se armen.</div>` : ""}
    <div class="card"><h3>${D.MONTHS[A.month - 1]} ${A.year}</h3>
    <div class="table-wrap"><table class="grid"><thead><tr>
      <th class="sticky">Empleado</th><th class="num">Libres del mes</th><th class="num">Horas de 4</th><th class="num">Días licencia</th>
      <th class="num">Mañanas</th><th class="num">Tardes</th><th class="num">Noches</th>${head}
    </tr></thead><tbody>${rows}</tbody></table></div>
    <p class="muted" style="font-size:13px">Libres en <span class="bad">rojo</span> = menos de los que le corresponden. Horas de 4 en <span class="bad">rojo</span> = pasó el máximo. Con licencia, los libres que le corresponden se calculan proporcionalmente.</p></div>`;
}

// ============ licencias ============

function renderLicenses() {
  const t = D.today();
  const empF = $("#lic-emp").value;
  const filt = $("#lic-filter .active").dataset.f;
  let rows = A.licenses.slice();
  if (empF) rows = rows.filter((l) => String(l.employee_id) === empF);
  if (filt === "current") rows = rows.filter((l) => l.end >= t).sort((a, b) => (a.start > b.start ? 1 : -1));
  const days = (l) => Math.round((D.parse(l.end) - D.parse(l.start)) / 86400000) + 1;
  const state = (l) => l.start > t ? `<span class="badge draft">Próxima</span>` : l.end < t ? `<span class="badge">Finalizada</span>` : `<span class="badge pub">En curso</span>`;
  const fmt = (s) => `${D.dm(D.parse(s))}/${s.slice(0, 4)}`;
  $("#lic-table").innerHTML = rows.length ? `<table class="grid"><thead><tr><th>Empleado</th><th>Desde</th><th>Hasta</th><th class="num">Días</th><th>Tipo</th><th>Estado</th><th>Nota</th><th></th></tr></thead><tbody>
    ${rows.map((l) => `<tr><td><b>${UI.esc(l.employee_name)}</b></td><td>${fmt(l.start)}</td><td>${fmt(l.end)}</td><td class="num">${days(l)}</td>
      <td>${UI.esc(l.kind)}</td><td>${state(l)}</td><td class="muted">${UI.esc(l.note || "")}</td>
      <td style="white-space:nowrap"><button class="btn small" data-edit="${l.id}">Editar</button> <button class="btn small danger" data-del="${l.id}">Borrar</button></td></tr>`).join("")}
    </tbody></table>` : `<div class="empty">No hay licencias para mostrar.</div>`;
}

function bindLicenses() {
  const form = $("#lic-form");
  const reset = () => {
    form.reset();
    A.licEditing = null;
    $("#lic-form-title").textContent = "Registrar licencia";
    $("#lic-cancel").hidden = true;
  };
  form.onsubmit = async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(form));
    try {
      if (A.licEditing) await API.put(`/api/admin/licenses/${A.licEditing}`, body);
      else await API.post("/api/admin/licenses", body);
      UI.toast("Licencia guardada");
      reset();
      await refreshLicenses();
    } catch (err) {
      UI.toast(err.message, 4000);
    }
  };
  $("#lic-cancel").onclick = reset;
  $("#lic-emp").onchange = renderLicenses;
  $("#lic-filter").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    $$("#lic-filter button").forEach((x) => x.classList.toggle("active", x === b));
    renderLicenses();
  };
  $("#lic-table").onclick = async (e) => {
    const ed = e.target.closest("[data-edit]");
    const del = e.target.closest("[data-del]");
    if (ed) {
      const l = A.licenses.find((x) => x.id === Number(ed.dataset.edit));
      for (const k of ["employee_id", "start", "end", "kind", "note"]) form.elements[k].value = l[k] ?? "";
      A.licEditing = l.id;
      $("#lic-form-title").textContent = `Editando licencia de ${l.employee_name}`;
      $("#lic-cancel").hidden = false;
      form.scrollIntoView({ behavior: "smooth" });
    }
    if (del) {
      const l = A.licenses.find((x) => x.id === Number(del.dataset.del));
      if (!(await UI.confirm("Borrar licencia", `¿Borrar la licencia de <b>${UI.esc(l.employee_name)}</b> (${l.start} a ${l.end})? No se puede deshacer.`, "Borrar", true))) return;
      await API.del(`/api/admin/licenses/${l.id}`);
      UI.toast("Licencia borrada");
      await refreshLicenses();
    }
  };
}

async function refreshLicenses() {
  A.licenses = await API.get("/api/admin/licenses");
  A.licDays = licenseDaysMap(A.licenses);
  renderLicenses();
  scheduleAnalyze();
  renderTurnos(true);
}

// ============ empleados ============

function renderEmployees() {
  const sel = (e, p) => {
    const v = e ? e.positions[p] || "" : "";
    return `<select class="input" data-pos="${p}"><option value="" ${v === "" ? "selected" : ""}>—</option>
      <option value="principal" ${v === "principal" ? "selected" : ""}>Principal</option>
      <option value="suplente" ${v === "suplente" ? "selected" : ""}>Suplente</option></select>`;
  };
  const row = (e) => `<tr data-id="${e ? e.id : ""}" ${e && !e.active ? 'style="opacity:.55"' : ""}>
      <td><input class="input" data-name value="${e ? UI.esc(e.name) : ""}" placeholder="Nombre" style="min-width:120px"></td>
      ${POSITIONS.map((p) => `<td>${sel(e, p)}</td>`).join("")}
      <td class="num"><input type="checkbox" data-enc ${e && e.positions[TASK] ? "checked" : ""} style="width:20px;height:20px" title="Encargado: puede asignarse otras tareas"></td>
      <td class="num"><input type="checkbox" data-active ${!e || e.active ? "checked" : ""} style="width:20px;height:20px"></td>
      <td><button class="btn small primary" data-save>${e ? "Guardar" : "Agregar"}</button></td></tr>`;
  $("#emp-table").innerHTML = `<table class="grid"><thead><tr><th>Nombre</th>${POSITIONS.map((p) => `<th>${POS_NAME[p]}</th>`).join("")}<th class="num">Encargado</th><th class="num">Activo</th><th></th></tr></thead>
    <tbody>${A.empList.map(row).join("")}${row(null)}</tbody></table>`;
  $("#emp-table").onclick = async (ev) => {
    const b = ev.target.closest("[data-save]");
    if (!b) return;
    const tr = b.closest("tr");
    const positions = {};
    $$("select[data-pos]", tr).forEach((s) => { if (s.value) positions[s.dataset.pos] = s.value; });
    if ($("[data-enc]", tr).checked) positions[TASK] = "principal";
    const body = { name: $("[data-name]", tr).value.trim(), active: $("[data-active]", tr).checked, positions };
    try {
      if (tr.dataset.id) await API.put(`/api/admin/employees/${tr.dataset.id}`, body);
      else await API.post("/api/admin/employees", body);
      UI.toast("Empleado guardado");
      await loadMeta();
      renderEmployees();
      scheduleAnalyze();
    } catch (err) {
      UI.toast(err.message, 4000);
    }
  };
}

// ============ configuración ============

async function renderConfig() {
  const s = A.settings;
  const f = $("#cfg-form");
  for (const k of ["max_horas4", "libres_objetivo", "bloque", "max_dias_seguidos", "tiempo_generador"]) f.elements[k].value = s[k];
  f.elements.cajeros_estrictos.checked = !!s.cajeros_estrictos;
  $("#staffing").innerHTML = `<table class="grid"><thead><tr><th>Puesto</th>${SHIFTS.map((x) => `<th class="num">${x.name}</th>`).join("")}</tr></thead><tbody>
    ${POSITIONS.map((p) => `<tr><td>${POS_NAME[p]}</td>${SHIFTS.map((x) => `<td class="num"><input class="input" type="number" min="0" max="9" style="width:70px" data-st="${x.key}|${p}" value="${s.staffing[x.key]?.[p] || 0}"></td>`).join("")}</tr>`).join("")}
    </tbody></table>`;
  f.onsubmit = async (e) => {
    e.preventDefault();
    const body = {};
    for (const k of ["max_horas4", "libres_objetivo", "bloque", "max_dias_seguidos", "tiempo_generador"]) body[k] = Number(f.elements[k].value);
    body.cajeros_estrictos = f.elements.cajeros_estrictos.checked;
    body.staffing = { T1: {}, T2: {}, T3: {} };
    $$("[data-st]", f).forEach((i) => { const [sh, p] = i.dataset.st.split("|"); body.staffing[sh][p] = Number(i.value); });
    try {
      A.settings = await API.put("/api/admin/settings", body);
      UI.toast("Configuración guardada");
      scheduleAnalyze();
    } catch (err) {
      UI.toast(err.message, 4000);
    }
  };
  const users = await API.get("/api/admin/users");
  $("#user-form [name=employee_id]").innerHTML = `<option value="">(ninguno)</option>${A.empList.filter((e) => e.active).map((e) => `<option value="${e.id}">${UI.esc(e.name)}</option>`).join("")}`;
  $("#users").innerHTML = `<table class="grid"><tbody>${users.map((u) => `<tr><td><b>${UI.esc(u.username)}</b></td><td class="muted">${u.employee_name ? "Empleado: " + UI.esc(u.employee_name) : ""}</td><td style="text-align:right"><button class="btn small danger" data-deluser="${u.id}">Quitar</button></td></tr>`).join("")}</tbody></table>`;
  $("#users").onclick = async (e) => {
    const b = e.target.closest("[data-deluser]");
    if (!b) return;
    if (!(await UI.confirm("Quitar administrador", "Esa persona ya no va a poder armar turnos.", "Quitar", true))) return;
    try { await API.del(`/api/admin/users/${b.dataset.deluser}`); renderConfig(); } catch (err) { UI.toast(err.message, 4000); }
  };
  $("#user-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await API.post("/api/admin/users", Object.fromEntries(new FormData(e.target)));
      e.target.reset();
      UI.toast("Administrador agregado (deberá cambiar la contraseña)");
      renderConfig();
    } catch (err) { UI.toast(err.message, 4000); }
  };
  $("#pwd-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await API.put("/api/admin/password", Object.fromEntries(new FormData(e.target)));
      e.target.reset();
      $("#pwd-alert").innerHTML = "";
      UI.toast("Contraseña cambiada");
    } catch (err) { UI.toast(err.message, 4000); }
  };
}

bindLicenses();
boot().catch((e) => UI.toast(e.message, 5000));
