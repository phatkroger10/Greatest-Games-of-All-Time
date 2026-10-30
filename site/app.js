(() => {
  "use strict";

  const SPORT = "cfb";
  const PAGE = 50;
  let WEIGHTS_KEY = `gg-weights-${SPORT}`;

  const $ = (id) => document.getElementById(id);
  const el = {
    from: $("f-from"), to: $("f-to"), type: $("f-type"), week: $("f-week"), team: $("f-team"),
    conf: $("f-conf"), stage: $("f-stage"), grade: $("f-grade"), sort: $("f-sort"),
    fbs: $("t-fbs"), ranked: $("t-ranked"), ot: $("t-ot"), upset: $("t-upset"),
    comeback: $("t-comeback"), rivalry: $("t-rivalry"),
  };
  const TOGGLES = ["fbs", "ranked", "ot", "upset", "comeback", "rivalry"];
  const DEFAULT_ON = {};

  let meta, games = [], weights = [], defaultWeights = [], filtered = [], shown = PAGE;
  const timelineCache = {};

  // ------------------------------------------------------------ helpers

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = {
    get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
    del(k) { try { localStorage.removeItem(k); } catch { /* storage unavailable */ } },
  };
  const fmtDate = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  const gradeClass = (g) => "g-" + g[0];
  const winner = (g) => (g.home.pts > g.away.pts ? g.home : g.away);
  const loser = (g) => (g.home.pts > g.away.pts ? g.away : g.home);
  const clockLeft = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

  function logo(team) {
    const t = meta.teams[team.id] || {};
    const dark = matchMedia("(prefers-color-scheme: dark)").matches && document.documentElement.dataset.theme !== "light";
    const src = (dark && t.logoDark) || t.logo;
    const fb = `<span class="logo-fallback" style="background:${esc(t.color || "#777")}">${esc((t.abbr || team.name).slice(0, 3))}</span>`;
    if (!src) return fb;
    return `<img src="${esc(src)}" alt="" loading="lazy" onerror="this.outerHTML=this.dataset.fb" data-fb="${esc(fb)}">`;
  }

  // ------------------------------------------------------------ scoring

  function computeScores() {
    const total = weights.reduce((a, b) => a + b, 0) || 1;
    for (const g of games) g.score = (100 * g.cats.reduce((s, c, i) => s + c * weights[i], 0)) / total;
    const order = [...games].sort((a, b) => b.score - a.score);
    const grades = meta.rubric.grades;
    order.forEach((g, i) => {
      const pct = ((i + 1) / order.length) * 100;
      g.overall = i + 1;
      g.grade = (grades.find((x) => pct <= x.topPercent) || grades[grades.length - 1]).grade;
    });
  }

  // ------------------------------------------------------------ filters

  function readFilters() {
    const f = {
      from: +el.from.value, to: +el.to.value, type: el.type.value, week: el.week.value,
      team: el.team.value.trim().toLowerCase(), conf: el.conf.value, stage: el.stage.value,
      grade: el.grade.value, sort: el.sort.value,
    };
    for (const t of TOGGLES) f[t] = el[t].checked;
    return f;
  }

  function gradeRank(g) {
    return meta.rubric.grades.findIndex((x) => x.grade === g);
  }

  function applyFilters() {
    const f = readFilters();
    const lo = Math.min(f.from, f.to), hi = Math.max(f.from, f.to);
    const maxGrade = f.grade ? gradeRank(f.grade) : Infinity;
    filtered = games.filter((g) => {
      if (g.season < lo || g.season > hi) return false;
      if (f.type && g.type !== f.type) return false;
      if (f.week === "post" && g.type !== "post") return false;
      if (f.week && f.week !== "post" && (g.type !== "reg" || g.week !== +f.week)) return false;
      if (f.team && !g.home.name.toLowerCase().includes(f.team) && !g.away.name.toLowerCase().includes(f.team)) return false;
      if (f.conf && g.home.conf !== f.conf && g.away.conf !== f.conf) return false;
      if (f.stage === "playoff" && !["title", "cfp_sf", "cfp_qf", "cfp_r1"].includes(g.stage)) return false;
      if (f.stage === "major" && !["title", "cfp_sf", "cfp_qf", "cfp_r1", "major_bowl"].includes(g.stage)) return false;
      if (f.stage === "bowl" && g.type !== "post") return false;
      if (f.stage === "conf_champ" && g.stage !== "conf_champ") return false;
      if (gradeRank(g.grade) > maxGrade) return false;
      if (f.fbs && !(g.home.fbs && g.away.fbs)) return false;
      if (f.ranked && !(g.home.rank && g.away.rank)) return false;
      if (f.ot && !g.m.ot) return false;
      if (f.upset && !g.tags.includes("Upset")) return false;
      if (f.comeback && g.m.comeback < 14) return false;
      if (f.rivalry && !g.tags.includes("Rivalry")) return false;
      return true;
    });

    const by = {
      score: (a, b) => b.score - a.score,
      newest: (a, b) => b.date.localeCompare(a.date) || b.score - a.score,
      oldest: (a, b) => a.date.localeCompare(b.date) || b.score - a.score,
      margin: (a, b) => a.m.margin - b.m.margin || b.m.ot - a.m.ot || b.score - a.score,
      leadChanges: (a, b) => b.m.leadChanges - a.m.leadChanges || b.score - a.score,
      comeback: (a, b) => b.m.comeback - a.m.comeback || b.score - a.score,
      upset: (a, b) => (a.m.winProb ?? 1) - (b.m.winProb ?? 1) || b.score - a.score,
      total: (a, b) => b.m.total - a.m.total || b.score - a.score,
    }[f.sort] || ((a, b) => b.score - a.score);
    filtered.sort(by);
    shown = PAGE;
    writeUrl(f);
    render();
  }

  function writeUrl(f) {
    const p = new URLSearchParams();
    const [first, last] = [meta.seasons[0], meta.seasons[meta.seasons.length - 1]];
    if (f.from !== first) p.set("from", f.from);
    if (f.to !== last) p.set("to", f.to);
    for (const k of ["type", "week", "conf", "stage", "grade"]) if (f[k]) p.set(k, f[k]);
    if (el.team.value.trim()) p.set("team", el.team.value.trim());
    if (f.sort !== "score") p.set("sort", f.sort);
    for (const t of TOGGLES) if (f[t] !== !!DEFAULT_ON[t]) p.set(t, f[t] ? "1" : "0");
    const qs = p.toString();
    history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
  }

  function readUrl() {
    const p = new URLSearchParams(location.search);
    const set = (k, node) => { if (p.has(k)) node.value = p.get(k); };
    set("from", el.from); set("to", el.to);
    for (const k of ["type", "week", "conf", "stage", "grade", "sort", "team"]) set(k, el[k]);
    for (const t of TOGGLES) if (p.has(t)) el[t].checked = p.get(t) === "1";
  }

  function resetFilters() {
    el.from.value = meta.seasons[0];
    el.to.value = meta.seasons[meta.seasons.length - 1];
    for (const k of ["type", "week", "conf", "stage", "grade", "team"]) el[k].value = "";
    el.sort.value = "score";
    for (const t of TOGGLES) el[t].checked = !!DEFAULT_ON[t];
    applyFilters();
  }

  // ------------------------------------------------------------ list

  function teamLine(g, side) {
    const t = g[side];
    const lost = winner(g) !== t;
    const rk = t.rank ? `<span class="rk">${t.rank}</span>` : "";
    return `<div class="team-line${lost ? " lost" : ""}">${logo(t)}<span class="team-name">${rk}${esc(t.name)}</span><span class="pts">${t.pts}</span></div>`;
  }

  function metaLine(g) {
    const wk = g.type === "post" ? "Postseason" : `Week ${g.week}`;
    const what = g.notes || g.stageLabel || "";
    return [fmtDate(g.date), wk, what].filter(Boolean).map(esc).join(" · ");
  }

  function tagsHtml(g) {
    const hot = new Set(["Walk-off", "Upset"]);
    const items = [];
    if (g.stageLabel && g.stage !== "bowl") items.push(`<span class="tag hot">${esc(g.stageLabel)}</span>`);
    for (const t of g.tags) items.push(`<span class="tag${hot.has(t) || /OT$|^Walk-off/.test(t) ? " hot" : ""}">${esc(t)}</span>`);
    return items.length ? `<div class="tags">${items.join("")}</div>` : "";
  }

  function barsHtml(g) {
    const short = ["Close", "Drama", "Finish", "Stakes", "Upset", "Points"];
    return `<div class="bars" aria-hidden="true">${g.cats.map((c, i) =>
      `<span>${short[i]}</span><span class="bar"><i style="width:${Math.round(c * 100)}%"></i></span>`).join("")}</div>`;
  }

  function render() {
    const list = $("list");
    const f = readFilters();
    $("count").textContent = `${filtered.length.toLocaleString()} game${filtered.length === 1 ? "" : "s"}`;
    if (!filtered.length) {
      list.innerHTML = `<li class="empty">No games match these filters.</li>`;
      $("btn-more").hidden = true;
      return;
    }
    const showRank = f.sort === "score";
    list.innerHTML = filtered.slice(0, shown).map((g, i) => `
      <li><button class="game" data-id="${g.id}">
        <span class="rank-no">${showRank ? i + 1 : ""}</span>
        <span class="grade ${gradeClass(g.grade)}"><b>${g.grade}</b><small>${g.score.toFixed(1)}</small></span>
        <span class="matchup">
          ${teamLine(g, "away")}
          ${teamLine(g, "home")}
          <span class="meta-line">${metaLine(g)}</span>
          ${tagsHtml(g)}
        </span>
        ${barsHtml(g)}
      </button></li>`).join("");
    $("btn-more").hidden = shown >= filtered.length;
  }

  // ------------------------------------------------------------ detail

  async function timelineFor(g) {
    if (!timelineCache[g.season]) {
      timelineCache[g.season] = fetch(`data/${SPORT}/timelines/${g.season}.json`).then((r) => r.json()).catch(() => ({}));
    }
    return (await timelineCache[g.season])[g.id];
  }

  function chartSvg(g, tl) {
    const W = 700, H = 220, padL = 34, padR = 10, padT = 12, padB = 26;
    const states = tl.s, end = tl.end;
    const margins = states.map(([, h, a]) => h - a);
    const maxAbs = Math.max(7, ...margins.map(Math.abs));
    const yMax = Math.ceil(maxAbs / 7) * 7;
    const x = (t) => padL + ((W - padL - padR) * t) / end;
    const y = (m) => padT + ((H - padT - padB) * (yMax - m)) / (2 * yMax);

    let d = `M${x(0)},${y(0)}`;
    for (let i = 1; i < states.length; i++) d += ` H${x(states[i][0])} V${y(margins[i])}`;
    d += ` H${x(end)}`;
    const area = `${d} V${y(0)} H${x(0)} Z`;

    const grid = [];
    for (let m = -yMax; m <= yMax; m += 7) {
      grid.push(`<line class="${m === 0 ? "zero" : "grid"}" x1="${padL}" x2="${W - padR}" y1="${y(m)}" y2="${y(m)}"/>`);
      grid.push(`<text x="${padL - 6}" y="${y(m) + 4}" text-anchor="end">${m === 0 ? "0" : Math.abs(m)}</text>`);
    }
    const marks = [[0, "Q1"], [900, "Q2"], [1800, "Q3"], [2700, "Q4"]];
    for (let t = 3600, k = 1; t < end; t += 300, k++) marks.push([t, k === 1 ? "OT" : `${k}OT`]);
    for (const [t, label] of marks) {
      grid.push(`<line class="grid" x1="${x(t)}" x2="${x(t)}" y1="${padT}" y2="${H - padB}"/>`);
      grid.push(`<text x="${x(t) + 4}" y="${H - 8}">${label}</text>`);
    }
    return `
      <svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Score margin over the course of the game">
        <defs>
          <clipPath id="clip-top"><rect x="0" y="0" width="${W}" height="${y(0)}"/></clipPath>
          <clipPath id="clip-bot"><rect x="0" y="${y(0)}" width="${W}" height="${H}"/></clipPath>
        </defs>
        ${grid.join("")}
        <path d="${area}" fill="var(--home)" opacity=".18" clip-path="url(#clip-top)"/>
        <path d="${area}" fill="var(--away)" opacity=".18" clip-path="url(#clip-bot)"/>
        <path d="${d}" fill="none" stroke="var(--home)" stroke-width="2.2" clip-path="url(#clip-top)"/>
        <path d="${d}" fill="none" stroke="var(--away)" stroke-width="2.2" clip-path="url(#clip-bot)"/>
      </svg>
      <div class="legend">
        <span><i style="background:var(--home)"></i>${esc(g.home.name)} ahead (above line)</span>
        <span><i style="background:var(--away)"></i>${esc(g.away.name)} ahead (below line)</span>
        ${g.m.detail === "quarters" ? "<span>Quarter-by-quarter only for this game</span>" : ""}
      </div>`;
  }

  function linescoreHtml(g) {
    const hl = g.home.line || [], al = g.away.line || [];
    if (!hl.length) return "";
    const heads = hl.map((_, i) => (i < 4 ? i + 1 : i === 4 ? "OT" : `${i - 3}OT`));
    const row = (t, l) => `<tr><td>${t.rank ? `<span class="rk">${t.rank}</span> ` : ""}${esc(t.name)}</td>${l.map((v) => `<td>${v ?? "–"}</td>`).join("")}<td class="final">${t.pts}</td></tr>`;
    return `<table class="linescore"><thead><tr><th></th>${heads.map((h) => `<th>${h}</th>`).join("")}<th>F</th></tr></thead>
      <tbody>${row(g.away, al)}${row(g.home, hl)}</tbody></table>`;
  }

  function factsHtml(g) {
    const m = g.m, w = winner(g);
    const facts = [
      [m.margin, "Final margin"],
      [m.leadChanges, "Lead changes"],
      [m.ties, "Times tied"],
      [m.comeback ? `${m.comeback}` : "0", `Biggest deficit ${esc(w.name)} overcame`],
    ];
    if (m.ot) facts.push([m.ot === 1 ? "OT" : `${m.ot}OT`, "Overtime"]);
    else if (m.decidedLeft != null) facts.push([clockLeft(m.decidedLeft), "Left when the winning lead was taken"]);
    if (m.winType) facts.push([{ TD: "TD", FG: "FG", Safety: "Safety" }[m.winType], "How the winning lead was taken"]);
    if (m.winProb != null) facts.push([`${Math.round(m.winProb * 100)}%`, `${esc(w.name)}'s pregame win chance (${m.winProbSrc === "spread" ? "Vegas" : "Elo"})`]);
    if (m.spread != null) {
      const fav = m.spread < 0 ? g.home : g.away;
      facts.push([Math.abs(m.spread) === 0 ? "PK" : `-${Math.abs(m.spread)}`, `Spread (${esc(fav.name)} favored)`]);
    }
    if (m.excitement != null) facts.push([m.excitement.toFixed(1), "CFBD excitement index"]);
    facts.push([m.total, "Total points"]);
    return `<div class="facts">${facts.map(([b, s]) => `<div class="fact"><b>${b}</b><span>${s}</span></div>`).join("")}</div>`;
  }

  function categoriesHtml(g) {
    const total = weights.reduce((a, b) => a + b, 0) || 1;
    return `<div class="cat-table">${meta.rubric.categories.map((c, i) => {
      const pts = (100 * g.cats[i] * weights[i]) / total;
      return `<span title="${esc(c.description)}">${esc(c.label)}</span>
        <span class="bar"><i style="width:${Math.round(g.cats[i] * 100)}%"></i></span>
        <span class="num">${pts.toFixed(1)} / ${((100 * weights[i]) / total).toFixed(0)}</span>`;
    }).join("")}</div>`;
  }

  async function openDetail(id) {
    const g = games.find((x) => x.id === id);
    if (!g) return;
    const w = winner(g), l = loser(g);
    const where = [g.venue, g.neutral ? "neutral site" : null].filter(Boolean).join(", ");
    $("d-body").innerHTML = `
      <div class="d-top">
        <span class="grade ${gradeClass(g.grade)}"><b>${g.grade}</b><small>${g.score.toFixed(1)}</small></span>
        <div>
          <h2 class="d-title" id="d-title">${esc(w.name)} ${w.pts}, ${esc(l.name)} ${l.pts}</h2>
          <div class="d-sub">${metaLine(g)}${where ? " · " + esc(where) : ""}</div>
          <div class="d-sub">#${g.overall.toLocaleString()} of ${games.length.toLocaleString()} games graded</div>
          ${tagsHtml(g)}
        </div>
      </div>
      <div class="d-section"><h3>Score</h3>${linescoreHtml(g)}</div>
      <div class="d-section"><h3>Game flow</h3><div id="d-chart">Loading…</div></div>
      <div class="d-section"><h3>How it graded</h3>${categoriesHtml(g)}</div>
      <div class="d-section"><h3>Key numbers</h3>${factsHtml(g)}</div>`;
    const dlg = $("detail");
    if (!dlg.open) dlg.showModal();
    const tl = await timelineFor(g);
    const box = $("d-chart");
    if (box) box.innerHTML = tl ? chartSvg(g, tl) : "No scoring timeline for this game.";
  }

  // ------------------------------------------------------------ rubric sliders

  function buildSliders() {
    const box = $("rubric-sliders");
    box.innerHTML = meta.rubric.categories.map((c, i) => `
      <label class="slider">
        <span class="slider-head">${esc(c.label)} <output id="w-out-${i}"></output></span>
        <input type="range" min="0" max="50" step="1" value="${weights[i]}" data-i="${i}">
        <small>${esc(c.description)}</small>
      </label>`).join("");
    updateSliderLabels();
    let t;
    box.addEventListener("input", (e) => {
      const i = e.target.dataset.i;
      if (i == null) return;
      weights[+i] = +e.target.value;
      updateSliderLabels();
      clearTimeout(t);
      t = setTimeout(() => { store.set(WEIGHTS_KEY, weights); computeScores(); applyFilters(); }, 120);
    });
  }

  function updateSliderLabels() {
    const total = weights.reduce((a, b) => a + b, 0) || 1;
    weights.forEach((w, i) => { $(`w-out-${i}`).textContent = `${Math.round((100 * w) / total)}%`; });
  }

  // ------------------------------------------------------------ setup

  function fillSelects() {
    const opt = (v, label = v) => `<option value="${esc(v)}">${esc(label)}</option>`;
    const seasons = meta.seasons;
    el.from.innerHTML = seasons.map((s) => opt(s)).join("");
    el.to.innerHTML = [...seasons].reverse().map((s) => opt(s)).join("");
    el.from.value = seasons[0];
    el.to.value = seasons[seasons.length - 1];
    const maxWeek = Math.max(...games.filter((g) => g.type === "reg").map((g) => g.week));
    el.week.innerHTML = opt("", "All weeks") + Array.from({ length: maxWeek }, (_, i) => opt(i + 1, `Week ${i + 1}`)).join("") + opt("post", "Bowls & playoff");
    el.conf.innerHTML = opt("", "Any") + meta.conferences.map((c) => opt(c)).join("");
    el.grade.innerHTML = opt("", "Any") + meta.rubric.grades.filter((g) => g.grade !== "F").map((g) => opt(g.grade, `${g.grade} or better`)).join("");
    const names = [...new Set(games.flatMap((g) => [g.home.name, g.away.name]))].sort();
    $("team-list").innerHTML = names.map((n) => `<option value="${esc(n)}">`).join("");
  }

  function bind() {
    for (const k of ["from", "to", "type", "week", "conf", "stage", "grade", "sort", ...TOGGLES]) el[k].addEventListener("change", applyFilters);
    let t;
    el.team.addEventListener("input", () => { clearTimeout(t); t = setTimeout(applyFilters, 200); });
    $("btn-more").addEventListener("click", () => { shown += PAGE; render(); });
    $("btn-reset").addEventListener("click", resetFilters);
    $("btn-rubric").addEventListener("click", (e) => {
      const r = $("rubric");
      r.hidden = !r.hidden;
      e.target.setAttribute("aria-expanded", String(!r.hidden));
    });
    $("btn-rubric-reset").addEventListener("click", () => {
      weights = [...defaultWeights];
      store.del(WEIGHTS_KEY);
      document.querySelectorAll("#rubric-sliders input").forEach((inp, i) => { inp.value = weights[i]; });
      updateSliderLabels();
      computeScores();
      applyFilters();
    });
    $("list").addEventListener("click", (e) => {
      const b = e.target.closest(".game");
      if (b) openDetail(+b.dataset.id);
    });
    $("d-close").addEventListener("click", () => $("detail").close());
    $("detail").addEventListener("click", (e) => { if (e.target.id === "detail") $("detail").close(); });
  }

  async function init() {
    const [m, g] = await Promise.all([
      fetch(`data/${SPORT}/meta.json`).then((r) => r.json()),
      fetch(`data/${SPORT}/games.json`).then((r) => r.json()),
    ]);
    meta = m;
    games = g;
    defaultWeights = meta.rubric.categories.map((c) => c.weight);
    // Saved slider weights are tied to the defaults, so changing the defaults resets everyone.
    WEIGHTS_KEY = `gg-weights-${SPORT}-${defaultWeights.join("-")}`;
    const saved = store.get(WEIGHTS_KEY);
    weights = Array.isArray(saved) && saved.length === defaultWeights.length ? saved : [...defaultWeights];

    const first = meta.seasons[0], last = meta.seasons[meta.seasons.length - 1];
    $("intro-sub").textContent = `${games.length.toLocaleString()} college football games from ${first} to ${last}, scored 0–100 on competitiveness, drama, finish, stakes, upset and fireworks.`;
    $("generated").textContent = `Updated ${new Date(meta.generatedAt).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}.`;

    fillSelects();
    readUrl();
    buildSliders();
    bind();
    computeScores();
    applyFilters();
  }

  init().catch((err) => {
    console.error(err);
    $("intro-sub").textContent = "Couldn't load the game data. Try refreshing the page.";
  });
})();
