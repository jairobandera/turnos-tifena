// Render de la planilla (mismo formato que el papel) compartido por la vista pública y el editor.
const SHIFTS = [
  { key: "T3", head: "TURNO 3 (Noche)", name: "Noche", hours: "22:00 a 06:00" },
  { key: "T1", head: "TURNO 1 (Mañana)", name: "Mañana", hours: "06:00 a 14:00" },
  { key: "T2", head: "TURNO 2 (Tarde)", name: "Tarde", hours: "14:00 a 22:00" },
];
const SHIFT_BY_KEY = Object.fromEntries(SHIFTS.map((s) => [s.key, s]));
const POSITIONS = ["cajero", "shop", "pistero", "auxiliar"];
const POS_NAME = { cajero: "Cajero", shop: "Cajero Shop", pistero: "Pistero", auxiliar: "Auxiliar", otro: "Otras tareas" };
const TASK = "otro"; // trabaja ese turno pero no ocupa puesto (ej. corte de pasto)

function posLabel(shift, pos) {
  if (pos === "cajero") return shift === "T3" ? "CAJERO:" : "CAJERO PISTA:";
  return { shop: "CAJERO SHOP:", pistero: "PISTERO:", auxiliar: "AUXILIAR:", otro: "OTRAS TAREAS:" }[pos];
}

/** Horarios especiales por cierres: {fecha: {turno: null (cerrado) | "20:00 a 06:00"}} */
function dayIsClosed(special, date) {
  const sp = special && special[date];
  return !!sp && ["T3", "T1", "T2"].every((k) => k in sp && sp[k] === null);
}

function posFullName(shift, pos, note) {
  if (pos === TASK) return note ? `Otras tareas: ${note}` : "Otras tareas";
  return pos === "cajero" && shift !== "T3" ? "Cajero Pista" : POS_NAME[pos];
}

/** Arma un mapa {empId: Set(fechas)} a partir de las licencias. */
function licenseDaysMap(licenses) {
  const map = new Map();
  for (const l of licenses) {
    const set = map.get(l.employee_id) || new Set();
    for (const d of D.range(D.parse(l.start), D.parse(l.end))) set.add(d);
    map.set(l.employee_id, set);
  }
  return map;
}

/**
 * ctx = { emps: Map(id -> empleado), staffing, licDays: Map, editable, highlight, issues: [], unpublished: bool }
 * assigns: asignaciones de ese día; en el editor cada una trae _i (índice en el estado).
 */
function renderDay(date, assigns, ctx) {
  const E = UI.esc;
  const issues = (ctx.issues || []).filter((i) => i.date === date);
  const issueFor = (eid) => {
    const mine = issues.filter((i) => i.employee_id === eid);
    if (mine.some((i) => i.level === "error")) return "err";
    if (mine.some((i) => i.level === "warn")) return "warn";
    return "";
  };
  const worked = new Set(assigns.map((a) => a.employee_id));

  const chip = (a, shift, pos) => {
    const emp = ctx.emps.get(a.employee_id);
    const name = emp ? emp.name : `#${a.employee_id}`;
    const cls = ["chip"];
    if (ctx.highlight === a.employee_id) cls.push("me");
    if (ctx.changed && ctx.changed.has(`${date}|${shift}|${pos}|${a.employee_id}`)) cls.push("new");
    const st = issueFor(a.employee_id);
    if (st && ctx.editable) cls.push(st);
    let tags = "";
    const note = pos === TASK && a.note ? ` <span class="tagmini">${E(a.note)}</span>` : "";
    if (ctx.editable && emp && pos !== TASK) {
      const lvl = emp.positions[pos];
      if (!lvl) tags += `<span class="tagmini out" title="No es su puesto">FUERA</span>`;
      else if (lvl === "suplente") tags += `<span class="tagmini sup" title="Suplente">SUP</span>`;
      else if (lvl === "ocasional") tags += `<span class="tagmini sup" title="Puesto ocasional">${pos === "auxiliar" ? "LIMPIEZA" : "OCAS"}</span>`;
    }
    tags = note + tags;
    if (!ctx.editable) return `<li class="${cls.join(" ")}">${E(name)}${note}</li>`;
    return `<li class="${cls.join(" ")}" data-idx="${a._i}" data-emp="${a.employee_id}">
      ${a.locked ? '<span class="lock" title="Fijo: el generador no lo cambia">🔒</span>' : ""}
      <span class="name">${E(name)}</span>${tags}
      <button class="x" data-remove="${a._i}" title="Quitar" aria-label="Quitar ${E(name)}">✕</button></li>`;
  };

  const sp = (ctx.special && ctx.special[date]) || {};
  const allClosed = dayIsClosed(ctx.special, date);
  const shiftCol = (s) => {
    if (s.key in sp && sp[s.key] === null) {
      return `<section class="shift ${s.key} closed"><div class="shift-head">${s.head}<span class="hours">cerrado</span></div>
        <div class="shift-rows"><div class="closed-msg">CERRADO</div></div></section>`;
    }
    const hours = sp[s.key] || s.hours;
    // dotación del día: la normal + pisteros extra si es día especial
    const need = { ...((ctx.staffing && ctx.staffing[s.key]) || {}) };
    const extra = (ctx.extra && ctx.extra[date] && ctx.extra[date][s.key]) || 0;
    if (extra) need.pistero = (need.pistero || 0) + extra;
    const present = new Set(assigns.filter((a) => a.shift === s.key).map((a) => a.position));
    const rows = [...POSITIONS, TASK].filter((p) => (need[p] || 0) > 0 || present.has(p)).map((p) => {
      const list = assigns.filter((a) => a.shift === s.key && a.position === p).sort((a, b) => (a.ord || 0) - (b.ord || 0));
      const n = need[p] || 0;
      let inner = list.map((a) => chip(a, s.key, p)).join("");
      if (!ctx.editable && list.length < n && !ctx.unpublished && assigns.length) {
        inner += `<li class="chip missing">sin asignar</li>`.repeat(n - list.length);
      }
      const under = ctx.editable && list.length < n ? " under" : "";
      // "+ agregar" sólo donde falta gente (los refuerzos van en los días especiales)
      const showAdd = ctx.editable && p !== TASK && list.length < n;
      const add = showAdd ? `<button class="add" data-add="${date}|${s.key}|${p}">+ agregar</button>` : "";
      return `<div class="prow"><div class="plabel">${posLabel(s.key, p)}</div>
        <div><ul class="slot${under}" data-date="${date}" data-shift="${s.key}" data-pos="${p}">${inner}</ul>${add}</div></div>`;
    }).join("");
    const task = ctx.editable && ctx.canTask && !present.has(TASK) ? `<button class="add task" data-add="${date}|${s.key}|${TASK}">+ otra tarea</button>` : "";
    return `<section class="shift ${s.key}${sp[s.key] ? " special" : ""}"><div class="shift-head">${s.head}<span class="hours">${hours}${sp[s.key] ? " ⚠️" : ""}</span>${extra ? `<span class="badge refuerzo-badge">Refuerzo +${extra}</span>` : ""}</div>
      <div class="shift-rows">${rows}${task}</div></section>`;
  };

  // libres: licencias primero, igual que en la planilla
  let libresHtml = "";
  if ((assigns.length || ctx.editable) && !allClosed) {
    const free = [...ctx.emps.values()].filter((e) => e.active && !worked.has(e.id));
    const onLic = free.filter((e) => ctx.licDays.get(e.id)?.has(date));
    const rest = free.filter((e) => !onLic.includes(e));
    libresHtml = [...onLic, ...rest].map((e) => {
      const lic = onLic.includes(e);
      const cls = ["chip"];
      if (ctx.highlight === e.id) cls.push("me");
      if (lic) cls.push("lic");
      const tag = lic ? `<span class="tagmini lic">LIC</span>` : "";
      return ctx.editable
        ? `<li class="${cls.join(" ")}" data-emp="${e.id}" data-free="1"><span class="name">${E(e.name)}</span>${tag}</li>`
        : `<li class="${cls.join(" ")}">${E(e.name)}${tag}</li>`;
    }).join("");
  }

  let issuesHtml = "";
  if (ctx.editable && issues.length) {
    const visible = issues.filter((i) => i.level !== "info");
    if (visible.length) {
      const hasErr = visible.some((i) => i.level === "error");
      issuesHtml = `<details class="issues ${hasErr ? "has-err" : ""}"><summary>${hasErr ? "⛔" : "⚠️"} ${visible.length} aviso${visible.length > 1 ? "s" : ""}</summary>
        <ul>${visible.map((i) => `<li class="${i.level}">${E(i.msg)}${i.shift ? ` · ${SHIFT_BY_KEY[i.shift].name}` : ""}</li>`).join("")}</ul></details>`;
    }
  }

  const tag = ctx.unpublished ? `<span class="tag badge">Sin publicar</span>` : "";
  let sugHtml = "";
  if (ctx.editable && ctx.suggestions) {
    sugHtml = ctx.suggestions.map((x, i) => (x.date !== date ? "" : `<div class="suggest">💡 <span>${suggestionText(x)}. <small>${E(x.reasons.join(" · "))}</small></span>
      <button class="btn small" data-suggest="${i}">🔎 Simular</button></div>`)).join("");
  }
  return `<article class="day ${ctx.unpublished ? "unpublished" : ""}" data-date="${date}">
    <div class="day-title">${D.title(date)}${allClosed ? ' <span class="badge closed-badge">ESTACIÓN CERRADA</span>' : ""}${tag}</div>${sugHtml}
    <div class="day-body">${SHIFTS.map(shiftCol).join("")}
      <section class="shift libres"><div class="shift-head">LIBRES</div>
      <div class="shift-rows"><ul class="libre-list" data-date="${date}">${libresHtml}</ul></div></section>
    </div>${issuesHtml}</article>`;
}

/** Vista "solo mis días" para un empleado. */
function renderMyDays(dates, assignsByDay, empId, ctx) {
  const E = UI.esc;
  let counts = { T1: 0, T2: 0, T3: 0, L: 0, LIC: 0 };
  const items = dates.map((d) => {
    const dd = D.parse(d);
    const head = `<div class="d">${D.SHORT[D.wd(dd)]} ${D.dm(dd)}</div>`;
    if (ctx.unpublished && ctx.unpublished.has(d)) {
      return `<div class="myday L">${head}<div class="what muted">Sin publicar</div></div>`;
    }
    const mine = (assignsByDay.get(d) || []).filter((a) => a.employee_id === empId);
    if (!mine.length && dayIsClosed(ctx.special, d)) {
      return `<div class="myday L">${head}<div class="what">Estación cerrada</div></div>`;
    }
    if (!mine.length) {
      if (ctx.licDays.get(empId)?.has(d)) { counts.LIC++; return `<div class="myday LIC">${head}<div class="what">Licencia</div></div>`; }
      counts.L++;
      return `<div class="myday L">${head}<div class="what">Libre</div></div>`;
    }
    return mine.map((a) => {
      counts[a.shift]++;
      const s = SHIFT_BY_KEY[a.shift];
      const hrs = (ctx.special && ctx.special[d] && ctx.special[d][a.shift]) || s.hours;
      return `<div class="myday ${a.shift}">${head}<div class="what">${s.name} · ${hrs}<small>${E(posFullName(a.shift, a.position, a.note))}${a.shift === "T3" ? " (entra la noche anterior)" : ""}</small></div></div>`;
    }).join("");
  }).join("");
  const summary = `<div class="summary"><span class="pill">🌅 Mañanas: <b>${counts.T1}</b></span><span class="pill">🌇 Tardes: <b>${counts.T2}</b></span>
    <span class="pill">🌙 Noches: <b>${counts.T3}</b></span><span class="pill">🏖️ Libres: <b>${counts.L}</b></span>${counts.LIC ? `<span class="pill">📄 Licencia: <b>${counts.LIC}</b></span>` : ""}</div>`;
  return summary + `<div class="mylist">${items}</div>`;
}
