// The quiz builder (/lag): write a quiz in the browser, then save it to the server.
// Drafts live in localStorage while they are being written, so a closed tab or a
// reloaded phone keeps the work. The server checks the finished quiz exactly like
// an uploaded file (schemas.py), and its problems are shown next to the fields.
(() => {
  "use strict";

  const root = document.getElementById("builder");
  if (!root) return;
  root.hidden = false;

  const KEY = "kviss.utkast";
  const MAX_CATEGORIES = 12;
  const MAX_QUESTIONS = 20;
  const $ = (selector, el = root) => el.querySelector(selector);
  const views = { start: $("#builder-start"), editor: $("#builder-editor"), saved: $("#builder-saved") };
  const editor = views.editor;
  const sourceEl = document.getElementById("builder-source");
  const source = sourceEl ? JSON.parse(sourceEl.textContent) : null; // the stored quiz, on /lag/<slug>

  // --- drafts in localStorage ------------------------------------------------
  // Draft: { id, origin: slug of the stored quiz it was opened from or null,
  //          originTitle, updated: ISO time, quiz: the form (see fromQuiz) }.
  // Storage can be missing or full (private browsing); the builder still works,
  // the drafts are then only kept until the page is closed.

  let drafts = [];

  function readStored() {
    try {
      const list = JSON.parse(localStorage.getItem(KEY) || "[]");
      return Array.isArray(list) ? list.filter((d) => d && typeof d.id === "string" && d.quiz) : [];
    } catch (_) {
      return null;
    }
  }

  // Changes re-read the stored list first, so drafts written in another tab are kept.
  function writeDrafts(change) {
    drafts = readStored() ?? drafts;
    change();
    try {
      localStorage.setItem(KEY, JSON.stringify(drafts));
      return true;
    } catch (_) {
      return false;
    }
  }

  const putDraft = (draft) => writeDrafts(() => {
    const i = drafts.findIndex((d) => d.id === draft.id);
    if (i < 0) drafts.push(draft); else drafts[i] = draft;
  });
  const dropDraft = (id) => writeDrafts(() => { drafts = drafts.filter((d) => d.id !== id); });

  try {
    localStorage.setItem(`${KEY}.test`, "1");
    localStorage.removeItem(`${KEY}.test`);
  } catch (_) {
    $("#builder-no-storage").hidden = false;
  }
  drafts = readStored() ?? [];

  function newId() {
    return crypto.randomUUID ? crypto.randomUUID() : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  }

  // --- the form: the quiz as the inputs hold it --------------------------------
  // Every value is the text in its input, so a half-typed number survives a reload.
  // toQuiz turns it into quiz JSON for the server.

  const blankQuestion = (value) => ({
    value: value ? String(value) : "", question: "", answer: "",
    source: "", youtube: "", audio: "", start: "", end: "",
  });
  const blankCategory = () => ({ name: "", questions: [100, 200, 300, 400, 500].map(blankQuestion) });
  const text = (v) => (v == null ? "" : String(v));

  function fromQuiz(quiz) {
    return {
      title: text(quiz.title),
      slug: text(quiz.slug),
      players: (quiz.players || []).join("\n"),
      categories: (quiz.categories || [blankCategory()]).map((c) => ({
        name: text(c.name),
        questions: (c.questions || []).map((q) => ({
          value: text(q.value), question: text(q.question), answer: text(q.answer),
          source: q.youtube ? "youtube" : q.audio ? "audio" : "",
          youtube: text(q.youtube), audio: text(q.audio), start: text(q.start), end: text(q.end),
        })),
      })),
      finalOn: Boolean(quiz.final),
      final: { category: text(quiz.final?.category), question: text(quiz.final?.question), answer: text(quiz.final?.answer) },
    };
  }
  const blankForm = () => fromQuiz({});

  // A whole number, or what was typed (the server then says what's wrong with it).
  function wholeNumber(value) {
    value = value.trim();
    if (!value) return null;
    return /^-?\d+$/.test(value) ? Number(value) : value;
  }

  // Seconds into a clip: "75", "42.5" or "1:15". Empty means left out.
  function seconds(value) {
    value = value.trim().replace(",", ".");
    if (!value) return null;
    if (/^\d+(\.\d+)?$/.test(value)) return Number(value);
    const m = value.match(/^(\d+):([0-5]?\d(\.\d+)?)$/);
    return m ? Number(m[1]) * 60 + Number(m[2]) : value;
  }

  // A pasted YouTube link becomes the 11-character video ID the quiz needs.
  function youtubeId(value) {
    value = value.trim();
    if (/^[\w-]{11}$/.test(value)) return value;
    try {
      const url = new URL(value.includes("://") ? value : `https://${value}`);
      const id = url.hostname.endsWith("youtu.be")
        ? url.pathname.slice(1)
        : url.searchParams.get("v") || url.pathname.match(/^\/(?:shorts|embed|live)\/([^/]+)/)?.[1];
      if (id && /^[\w-]{11}$/.test(id)) return id;
    } catch (_) { /* not a link: leave it for the server to explain */ }
    return value;
  }

  function toQuiz(form) {
    const quiz = { title: form.title };
    if (form.slug) quiz.slug = form.slug;
    quiz.players = form.players.split("\n").map((p) => p.trim()).filter(Boolean);
    quiz.categories = form.categories.map((c) => ({
      name: c.name,
      questions: c.questions.map((q) => {
        const out = { value: wholeNumber(q.value), question: q.question, answer: q.answer };
        if (q.source === "youtube") out.youtube = youtubeId(q.youtube);
        if (q.source === "audio") out.audio = q.audio.trim();
        if (q.source) {
          const start = seconds(q.start);
          const end = seconds(q.end);
          if (start !== null) out.start = start;
          if (end !== null) out.end = end;
        }
        return out;
      }),
    }));
    if (form.finalOn) quiz.final = { ...form.final };
    return quiz;
  }

  // "categories.0.questions.2.value" -> form.categories[0].questions[2].value
  function setPath(obj, path, value) {
    const keys = path.split(".");
    const last = keys.pop();
    for (const key of keys) obj = obj[key];
    obj[last] = value;
  }
  const getPath = (obj, path) => path.split(".").reduce((o, key) => o?.[key], obj);

  // --- views -------------------------------------------------------------------

  let current = null; // the draft open in the editor
  let resumedId = null; // a draft of the quiz on /lag/<slug> that was there before the page opened

  function show(name) {
    for (const [key, el] of Object.entries(views)) el.hidden = key !== name;
    window.scrollTo(0, 0);
  }

  const when = (iso) => new Date(iso).toLocaleString("nb-NO", { dateStyle: "short", timeStyle: "short" });
  const count = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const draftHref = (id) => `#utkast/${id}`;

  function h(tag, attrs = {}, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === false || value == null) continue;
      if (key === "class") el.className = value;
      else if (key === "text") el.textContent = value;
      else if (key === "value") el.value = value;
      else el.setAttribute(key, value === true ? "" : value);
    }
    el.append(...children.filter((c) => c != null && c !== false));
    return el;
  }

  function showStart() {
    // A new draft nobody typed anything into isn't worth keeping.
    const blank = JSON.stringify(blankForm());
    if (drafts.some((d) => !d.origin && JSON.stringify(d.quiz) === blank)) {
      writeDrafts(() => { drafts = drafts.filter((d) => d.origin || JSON.stringify(d.quiz) !== blank); });
    }
    current = null;
    renderDrafts();
    show("start");
  }

  function renderDrafts() {
    const list = [...drafts].sort((a, b) => (b.updated || "").localeCompare(a.updated || ""));
    $("#builder-drafts").replaceChildren(...list.map((d) => {
      const questions = d.quiz.categories.reduce((n, c) => n + c.questions.length, 0);
      return h("li", { class: "builder-draft" },
        h("a", { class: "quiz-pick", href: draftHref(d.id) },
          h("strong", { text: d.quiz.title.trim() || "Uten tittel" }),
          h("span", { class: "muted", text: [
            count(d.quiz.categories.length, "kategori", "kategorier"),
            count(questions, "spørsmål", "spørsmål"),
            d.quiz.finalOn ? "finale" : null,
            d.origin ? `endrer «${d.originTitle || d.origin}»` : null,
            `sist endret ${when(d.updated)}`,
          ].filter(Boolean).join(" · ") })),
        h("button", { type: "button", class: "tool bad", "data-action": "draft-delete", "data-id": d.id,
          "aria-label": "Slett utkastet", text: "✕" }));
    }));
    $("#builder-no-drafts").hidden = list.length > 0;
  }

  function openEditor(draft) {
    current = draft;
    fillEditor();
    show("editor");
  }

  function fillEditor() {
    const form = current.quiz;
    editor.querySelectorAll("[data-path]").forEach((el) => {
      if (el.closest(".builder-categories")) return;
      const value = getPath(form, el.dataset.path);
      if (el.type === "checkbox") el.checked = Boolean(value); else el.value = value ?? "";
    });
    $(".builder-final", editor).disabled = !form.finalOn;
    const replaces = $(".builder-replaces", editor);
    replaces.hidden = !current.origin;
    replaces.replaceChildren();
    if (current.origin) {
      replaces.append(`Endrer den lagrede kvissen «${current.originTitle || current.origin}». Når du lagrer, `
        + "erstattes den. Spill som allerede er startet, beholder sin egen kopi.");
      if (source && source.slug === current.origin && resumedId === current.id) {
        replaces.append(" Du fortsetter på et utkast fra ", when(current.updated), ". ",
          h("button", { type: "button", class: "tool", "data-action": "reload-source",
            text: "Hent den lagrede versjonen" }));
      }
    }
    renderCategories();
    setStatus(current.updated ? `Utkast lagret ${when(current.updated)}` : "");
  }

  function renderCategories() {
    clearProblems();
    const cats = current.quiz.categories;
    $(".builder-categories", editor).replaceChildren(...cats.map(categoryEl));
    $(".builder-add-category", editor).disabled = cats.length >= MAX_CATEGORIES;
  }

  function moveButtons(action, index, length, label) {
    return [
      h("button", { type: "button", class: "tool", "data-action": `${action}-up`, "data-index": index,
        disabled: index === 0, "aria-label": `Flytt ${label} opp`, text: "↑" }),
      h("button", { type: "button", class: "tool", "data-action": `${action}-down`, "data-index": index,
        disabled: index === length - 1, "aria-label": `Flytt ${label} ned`, text: "↓" }),
      h("button", { type: "button", class: "tool bad", "data-action": `${action}-delete`, "data-index": index,
        "aria-label": `Slett ${label}`, text: "✕" }),
    ];
  }

  function categoryEl(cat, ci) {
    const path = `categories.${ci}`;
    const id = `b-cat-${ci}`;
    return h("fieldset", { class: "builder-category", "data-loc": path, "data-ci": ci },
      h("div", { class: "builder-row" },
        h("label", { for: id, class: "builder-cat-label", text: `Kategori ${ci + 1}` }),
        ...moveButtons("cat", ci, current.quiz.categories.length, "kategorien")),
      h("input", { id, type: "text", maxlength: 100, autocomplete: "off", placeholder: "Geografi",
        "data-path": `${path}.name`, value: cat.name }),
      h("ol", { class: "builder-questions" }, ...cat.questions.map((q, qi) => questionEl(q, ci, qi, cat.questions.length))),
      h("button", { type: "button", class: "tool", "data-action": "q-add", "data-ci": ci,
        disabled: cat.questions.length >= MAX_QUESTIONS, text: "＋ Spørsmål" }));
  }

  function questionEl(q, ci, qi, length) {
    const path = `categories.${ci}.questions.${qi}`;
    const field = (label, key, attrs = {}, tag = "input") => h("label", { class: `builder-field builder-field-${key}` },
      h("span", { text: label }),
      h(tag, { type: tag === "input" ? "text" : null, autocomplete: tag === "input" ? "off" : null,
        "data-path": `${path}.${key}`, value: q[key], ...attrs }));
    const music = h("details", { class: "builder-music", open: Boolean(q.source) },
      h("summary", { text: "♪ Musikk" }),
      h("label", { class: "builder-field" }, h("span", { text: "Spill av" }),
        h("select", { "data-path": `${path}.source` },
          h("option", { value: "", text: "Ingen musikk" }),
          h("option", { value: "youtube", text: "Et klipp fra YouTube" }),
          h("option", { value: "audio", text: "En lydfil på serveren" }))),
      field("YouTube-lenke eller video-ID", "youtube", { placeholder: "https://www.youtube.com/watch?v=…" }),
      field("Filnavn i media-mappen", "audio", { placeholder: "take-on-me.mp3" }),
      h("p", { class: "muted builder-audio-help",
        text: "Filen må ligge på serveren før du lagrer (.mp3, .m4a, .aac eller .wav)." }),
      h("div", { class: "builder-row builder-clip" },
        field("Start", "start", { placeholder: "0:00" }),
        field("Slutt", "end", { placeholder: "slutten" })));
    music.querySelector("select").value = q.source;
    showMusicFields(music, q.source);
    return h("li", { class: "builder-question", "data-loc": path },
      h("div", { class: "builder-row" },
        field("Poeng", "value", { type: "number", min: 1, step: 1, inputmode: "numeric", class: "builder-value" }),
        ...moveButtons("q", qi, length, "spørsmålet")),
      field("Spørsmål (hintet som vises)", "question", { rows: 2, maxlength: 1000 }, "textarea"),
      field("Svar", "answer", { maxlength: 1000 }),
      music);
  }

  function showMusicFields(music, sourceKind) {
    music.querySelector(".builder-field-youtube").hidden = sourceKind !== "youtube";
    music.querySelector(".builder-field-audio").hidden = sourceKind !== "audio";
    music.querySelector(".builder-audio-help").hidden = sourceKind !== "audio";
    music.querySelector(".builder-clip").hidden = !sourceKind;
    music.querySelector("summary").textContent = sourceKind === "youtube" ? "♪ Musikk · YouTube"
      : sourceKind === "audio" ? "♪ Musikk · lydfil" : "♪ Musikk";
  }

  // --- autosave ------------------------------------------------------------------

  let saveTimer = null;
  const statusEl = $(".builder-status", editor);
  const setStatus = (message) => { statusEl.textContent = message; };

  function saveSoon() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveNow, 400);
  }

  function saveNow() {
    clearTimeout(saveTimer);
    saveTimer = null;
    if (!current) return;
    current.updated = new Date().toISOString();
    setStatus(putDraft(current) ? `Utkast lagret ${when(current.updated)}` : "Utkastet kunne ikke lagres i nettleseren");
  }

  addEventListener("pagehide", () => { if (saveTimer) saveNow(); });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden" && saveTimer) saveNow();
  });

  // --- editing -----------------------------------------------------------------

  editor.addEventListener("input", (e) => {
    const el = e.target.closest("[data-path]");
    if (!el || !current) return;
    const value = el.type === "checkbox" ? el.checked : el.value;
    setPath(current.quiz, el.dataset.path, value);
    el.classList.remove("invalid");
    el.removeAttribute("aria-invalid");
    if (el.dataset.path === "finalOn") $(".builder-final", editor).disabled = !value;
    if (el.dataset.path.endsWith(".source")) showMusicFields(el.closest(".builder-music"), value);
    saveSoon();
  });

  // Show the ID as soon as a whole link has been pasted, so the host sees what is saved.
  editor.addEventListener("change", (e) => {
    const el = e.target;
    if (!el.dataset.path?.endsWith(".youtube")) return;
    const id = youtubeId(el.value);
    if (id !== el.value) {
      el.value = id;
      setPath(current.quiz, el.dataset.path, id);
      saveSoon();
    }
  });

  const hasText = (q) => [q.question, q.answer, q.youtube, q.audio].some((v) => v.trim());

  function swap(list, i, j) {
    if (j < 0 || j >= list.length) return;
    [list[i], list[j]] = [list[j], list[i]];
  }

  const actions = {
    "cat-up": (i) => swap(current.quiz.categories, i, i - 1),
    "cat-down": (i) => swap(current.quiz.categories, i, i + 1),
    "cat-delete": (i) => {
      const cat = current.quiz.categories[i];
      if ((cat.name.trim() || cat.questions.some(hasText))
          && !confirm(`Slette kategorien «${cat.name.trim() || `Kategori ${i + 1}`}» med alle spørsmålene?`)) return false;
      current.quiz.categories.splice(i, 1);
    },
    "q-up": (i, ci) => swap(current.quiz.categories[ci].questions, i, i - 1),
    "q-down": (i, ci) => swap(current.quiz.categories[ci].questions, i, i + 1),
    "q-delete": (i, ci) => {
      const questions = current.quiz.categories[ci].questions;
      if (hasText(questions[i]) && !confirm("Slette spørsmålet?")) return false;
      questions.splice(i, 1);
    },
    "q-add": (_, ci) => {
      const questions = current.quiz.categories[ci].questions;
      const last = Number(questions.at(-1)?.value);
      questions.push(blankQuestion(Number.isInteger(last) && last > 0 ? last + 100 : 100));
      return `[data-path="categories.${ci}.questions.${questions.length - 1}.question"]`;
    },
  };

  root.addEventListener("click", (e) => {
    const button = e.target.closest("button[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    if (action === "draft-delete") {
      const draft = drafts.find((d) => d.id === button.dataset.id);
      if (draft && confirm(`Slette utkastet «${draft.quiz.title.trim() || "Uten tittel"}»?`)) {
        dropDraft(draft.id);
        renderDrafts();
      }
      return;
    }
    if (action === "reload-source") {
      if (confirm("Forkaste endringene i utkastet og hente kvissen slik den er lagret?")) {
        current.quiz = fromQuiz(source);
        resumedId = null;
        saveNow();
        fillEditor();
      }
      return;
    }
    const category = button.closest("[data-ci]");
    const ci = category ? Number(category.dataset.ci) : null;
    const result = actions[action]?.(Number(button.dataset.index), ci);
    if (result === false) return;
    renderCategories();
    saveSoon();
    const focus = typeof result === "string" ? $(result, editor) : null;
    if (focus) {
      focus.scrollIntoView({ block: "center" });
      focus.focus({ preventScroll: true });
    }
  });

  $(".builder-add-category", editor).addEventListener("click", () => {
    const cats = current.quiz.categories;
    if (cats.length >= MAX_CATEGORIES) return;
    cats.push(blankCategory());
    renderCategories();
    saveSoon();
    const name = $(`[data-path="categories.${cats.length - 1}.name"]`, editor);
    name.scrollIntoView({ block: "start" });
    name.focus({ preventScroll: true });
  });

  $(".builder-new").addEventListener("click", () => {
    const draft = { id: newId(), origin: null, originTitle: null, updated: new Date().toISOString(), quiz: blankForm() };
    putDraft(draft);
    location.hash = draftHref(draft.id);
  });

  $(".builder-back", editor).addEventListener("click", () => {
    if (saveTimer) saveNow();
    if (location.pathname === root.dataset.homeUrl) location.hash = "";
    else location.href = root.dataset.homeUrl;
  });

  $(".builder-delete", editor).addEventListener("click", () => {
    if (!confirm("Slette utkastet? Det kan ikke angres.")) return;
    clearTimeout(saveTimer);
    saveTimer = null;
    dropDraft(current.id);
    location.href = root.dataset.homeUrl;
  });

  $(".builder-download", editor).addEventListener("click", () => {
    const quiz = toQuiz(current.quiz);
    const name = (quiz.slug || quiz.title.toLowerCase().replace(/[^a-z0-9æøå]+/g, "-").replace(/^-|-$/g, "")) || "kviss";
    const link = h("a", { href: URL.createObjectURL(new Blob([JSON.stringify(quiz, null, 2)], { type: "application/json" })),
      download: `${name}.json` });
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  });

  // --- problems from the server ---------------------------------------------------

  const problemsBox = $(".builder-problems", editor);

  function clearProblems() {
    problemsBox.hidden = true;
    problemsBox.querySelector("ul").replaceChildren();
    editor.querySelectorAll(".invalid").forEach((el) => {
      el.classList.remove("invalid");
      el.removeAttribute("aria-invalid");
    });
  }

  // ["categories", 0, "questions", 1, "answer"] -> the answer input; for a problem with a
  // whole question or category (e.g. "'end' must be after 'start'"), its box.
  function fieldFor(where) {
    for (let n = where.length; n > 0; n--) {
      const key = where.slice(0, n).join(".");
      const el = editor.querySelector(`[data-path="${key}"], [data-loc="${key}"]`);
      if (el) return el;
    }
    return null;
  }

  function showProblems(problems) {
    clearProblems();
    const items = problems.map(({ message, location: where = [] }) => {
      const el = fieldFor(where);
      if (!el) return h("li", { text: message });
      el.classList.add("invalid");
      el.setAttribute("aria-invalid", "true");
      el.closest("details")?.setAttribute("open", "");
      const link = h("a", { href: "#", class: "gold", text: message });
      link.addEventListener("click", (e) => {
        e.preventDefault();
        el.scrollIntoView({ block: "center" });
        (el.matches("input, textarea, select") ? el : el.querySelector("input, textarea, select"))
          ?.focus({ preventScroll: true });
      });
      return h("li", {}, link);
    });
    problemsBox.querySelector("ul").replaceChildren(...items);
    problemsBox.hidden = false;
    problemsBox.scrollIntoView({ block: "center" });
  }

  // --- saving to the server ---------------------------------------------------------

  const submit = editor.querySelector('button[type="submit"]');

  async function upload(overwrite) {
    clearProblems();
    submit.disabled = true;
    setStatus("Lagrer …");
    let resp;
    let body = null;
    try {
      resp = await fetch(root.dataset.saveUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ quiz: toQuiz(current.quiz), replaces: current.origin, overwrite }),
      });
      body = await resp.json().catch(() => null);
    } catch (_) {
      setStatus("");
      showProblems([{ message: "Fikk ikke kontakt med serveren. Utkastet er tatt vare på, så prøv igjen." }]);
      return;
    } finally {
      submit.disabled = false;
    }
    setStatus(`Utkast lagret ${when(current.updated)}`);
    if (resp.status === 409 && body?.conflict) {
      if (confirm(`Det finnes allerede en kviss med samme navn: «${body.conflict.title}». Vil du erstatte den?`)) {
        await upload(true);
      } else {
        showProblems([{ message: "Ikke lagret. Gi kvissen en annen tittel for å beholde begge.", location: ["title"] }]);
      }
      return;
    }
    if (resp.status === 413) {
      showProblems([{ message: "Kvissen er for stor (maks 1 MB)." }]);
      return;
    }
    if (!resp.ok || !body) {
      showProblems(body?.problems || [{ message: `Noe gikk galt (HTTP ${resp.status}). Utkastet er tatt vare på.` }]);
      return;
    }
    // Saved: the server has it now, so the draft has done its job.
    const form = current.quiz;
    const questions = form.categories.reduce((n, c) => n + c.questions.length, 0);
    dropDraft(current.id);
    current = null;
    $(".builder-saved-title").textContent = body.title;
    $(".builder-saved-what").textContent = [
      body.created ? "lagt til" : "erstattet kvissen med samme navn",
      count(form.categories.length, "kategori", "kategorier"), count(questions, "spørsmål", "spørsmål"),
    ].join(" · ");
    $(".builder-start-game").href = body.start;
    $(".builder-edit-again").href = body.edit;
    history.replaceState(null, "", root.dataset.homeUrl);
    show("saved");
  }

  editor.addEventListener("submit", (e) => {
    e.preventDefault();
    if (saveTimer) saveNow();
    upload(false);
  });

  // --- routing: #utkast/<id> opens a draft, no hash lists them -----------------------

  function route() {
    drafts = readStored() ?? drafts;
    const id = location.hash.match(/^#utkast\/([\w-]+)$/)?.[1];
    const draft = id && drafts.find((d) => d.id === id);
    if (draft) openEditor(draft); else showStart();
  }

  addEventListener("hashchange", route);
  addEventListener("storage", (e) => {
    if (e.key === KEY && !views.start.hidden) {
      drafts = readStored() ?? drafts;
      renderDrafts();
    }
  });

  // /lag/<slug>: continue the draft already made from that quiz, or start one from it.
  if (source && !location.hash) {
    let draft = drafts.find((d) => d.origin === source.slug);
    if (draft) {
      resumedId = draft.id;
    } else {
      draft = { id: newId(), origin: source.slug, originTitle: source.title,
        updated: new Date().toISOString(), quiz: fromQuiz(source) };
      putDraft(draft);
    }
    history.replaceState(null, "", draftHref(draft.id));
  }
  route();
})();
