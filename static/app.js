// Progressive enhancement: every feature here is optional. Without this file
// the app still works through plain links and form posts.
(() => {
  "use strict";

  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // --- Keep the phone awake while it is mirrored to the TV -----------------
  // The Wake Lock API needs HTTPS, and the lock is dropped whenever the page
  // is hidden, so request it again each time the page becomes visible.
  async function keepAwake() {
    if (!("wakeLock" in navigator) || document.visibilityState !== "visible") return;
    try { await navigator.wakeLock.request("screen"); } catch (_) { /* denied or unsupported: ignore */ }
  }
  keepAwake();
  document.addEventListener("visibilitychange", keepAwake);

  // --- Board tile zooms into the question (cross-document view transition) --
  // CSS gives the question page's clue the transition name "clue"; giving the
  // tapped tile the same name makes the browser morph one into the other.
  document.addEventListener("click", (e) => {
    const tile = e.target.closest("a.cell");
    if (tile) tile.style.viewTransitionName = "clue";
  });
  // Coming back with the browser's Back button can restore the old page from
  // cache with the name still set; clear it so names stay unique.
  // The same goes for forms already marked as submitted below.
  addEventListener("pageshow", () => {
    document.querySelectorAll("a.cell").forEach((t) => { t.style.viewTransitionName = ""; });
    document.querySelectorAll("form[data-sent]").forEach((f) => { delete f.dataset.sent; });
  });

  // --- Reveal the answer in place -------------------------------------------
  const reveal = document.querySelector("a.reveal");
  const answer = document.querySelector(".clue-answer[hidden]");
  if (reveal && answer) {
    reveal.addEventListener("click", (e) => {
      e.preventDefault();
      reveal.remove();
      answer.hidden = false;
      answer.classList.add("pop-in");
      // Judging a wrong answer reloads the page; keep the answer visible then.
      document.querySelectorAll('input[name="reveal"]').forEach((i) => { i.value = "1"; });
      history.replaceState(null, "", reveal.href);
    });
  }

  // --- Judge buttons: no double submits, a little haptic feedback -----------
  document.addEventListener("submit", (e) => {
    const form = e.target;
    if (form.dataset.sent) { e.preventDefault(); return; }
    form.dataset.sent = "1";
    const result = e.submitter && e.submitter.value;
    if (navigator.vibrate) navigator.vibrate(result === "wrong" ? [40, 60, 40] : 30);
  });

  // --- Animated numbers -----------------------------------------------------
  function countTo(el, from, to, delayMs = 0) {
    if (reduceMotion || from === to) { el.textContent = to; return; }
    el.textContent = from;
    const duration = 900;
    setTimeout(() => {
      const start = performance.now();
      const tick = (now) => {
        const t = Math.min(1, (now - start) / duration);
        const eased = 1 - Math.pow(1 - t, 3); // ease-out cubic
        el.textContent = Math.round(from + (to - from) * eased);
        if (t < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    }, delayMs);
  }

  // Scoreboard: animate from the scores this browser saw last time.
  const scoreEls = document.querySelectorAll(".score-value[data-score]");
  if (scoreEls.length) {
    let previous = null;
    try { previous = JSON.parse(sessionStorage.getItem("kviss-scores")); } catch (_) {}
    const current = [...scoreEls].map((el) => Number(el.dataset.score));
    scoreEls.forEach((el, i) => {
      if (!previous || previous.length !== current.length || previous[i] === current[i]) return;
      const card = el.closest(".score");
      card.classList.add(current[i] > previous[i] ? "flash-up" : "flash-down");
      countTo(el, previous[i], current[i], 250);
    });
    try { sessionStorage.setItem("kviss-scores", JSON.stringify(current)); } catch (_) {}
  }

  // Podium: the host reveals one place per tap (3rd, 2nd, then the winner).
  // Without this script the CSS plays the same reveal automatically instead.
  const final = document.querySelector(".final");
  if (final) {
    const page = document.body;
    const next = final.querySelector(".next-btn");
    const order = [3, 2, 1]
      .map((slot) => final.querySelector(`.step.slot-${slot}`))
      .filter(Boolean);
    let shown = 0;

    const label = () => {
      if (shown >= order.length) return;
      const step = order[shown];
      next.textContent = step.classList.contains("slot-1")
        ? "Avslør vinneren!"
        : `Avslør ${step.dataset.place}. plass`;
    };

    const advance = () => {
      if (shown >= order.length) return;
      const step = order[shown++];
      step.classList.add("go");
      const score = step.querySelector(".step-score");
      countTo(score, 0, Number(score.dataset.score), 700);
      if (navigator.vibrate) navigator.vibrate(30);
      if (shown === order.length) {
        page.classList.add("done");
        next.hidden = true;
      } else {
        label();
      }
    };

    page.classList.add("manual");
    order.forEach((step) => { step.querySelector(".step-score").textContent = "0"; });
    next.hidden = false;
    label();

    // Tap anywhere on the stage (or press Space / Enter / →) to reveal the next place.
    final.addEventListener("click", (e) => {
      if (!e.target.closest("a, form")) advance();
    });
    document.addEventListener("keydown", (e) => {
      // A focused button already turns Space/Enter into a click; don't count it twice.
      if (e.target.closest("button, a")) return;
      if ([" ", "Enter", "ArrowRight"].includes(e.key)) { e.preventDefault(); advance(); }
    });
  }
})();
