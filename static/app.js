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

  // --- Music questions: play a clip from a local file or a hidden YouTube video -
  // Both sources sit behind the same small interface, so the buttons, the stop
  // at `end` and the progress bar work the same way for each.
  const music = document.querySelector(".music");
  if (music) {
    const start = Number(music.dataset.start) || 0;
    const end = music.dataset.end ? Number(music.dataset.end) : null;
    const playBtn = music.querySelector(".music-play");
    const restartBtn = music.querySelector(".music-restart");
    const bar = music.querySelector(".music-progress");
    const status = music.querySelector(".music-status");
    let player = null; // { play, pause, seek(t), time(), duration(), playing() }

    music.querySelectorAll(".music-fallback").forEach((el) => el.remove());
    music.querySelector(".music-controls").hidden = false;

    const say = (text) => { status.textContent = text; status.hidden = !text; };
    const stopAt = () => end ?? (player.duration() || null);

    const paint = () => {
      if (!player) return;
      playBtn.textContent = player.playing() ? "❚❚ Pause" : "▶ Spill av";
      const stop = stopAt();
      if (stop > start) {
        const p = Math.min(1, Math.max(0, (player.time() - start) / (stop - start)));
        bar.hidden = false;
        bar.firstElementChild.style.width = `${p * 100}%`;
      }
    };

    setInterval(() => {
      if (!player || !player.playing()) return;
      if (end !== null && player.time() >= end) player.pause();
      paint();
    }, 200);

    const play = () => {
      const stop = stopAt();
      // Start over if the clip already played to its end (or is before its start).
      if (player.time() < start - 0.5 || (stop && player.time() >= stop - 0.3)) player.seek(start);
      say("");
      player.play();
    };
    const toggle = () => { if (!player) return; player.playing() ? player.pause() : play(); paint(); };

    const ready = (p) => {
      player = p;
      playBtn.disabled = restartBtn.disabled = false;
      say("");
    };

    playBtn.addEventListener("click", toggle);
    restartBtn.addEventListener("click", () => {
      if (!player) return;
      player.seek(start);
      play();
      paint();
    });
    // Space bar plays/pauses (handy with a Mac on HDMI).
    document.addEventListener("keydown", (e) => {
      if (e.key !== " " || e.target.closest("button, a, input")) return;
      e.preventDefault();
      toggle();
    });

    if (music.dataset.audio) {
      // The #t= media fragment makes the first play begin at `start`, even on an
      // iPhone that has not loaded the file yet (so seeking is not possible).
      const el = new Audio(`${music.dataset.audio}#t=${start}`);
      el.preload = "auto";
      el.addEventListener("error", () => say("Fant ikke lydfilen."));
      ["play", "pause", "ended", "loadedmetadata"].forEach((ev) => el.addEventListener(ev, paint));
      ready({
        play: () => el.play().catch(() => say("Lyden startet ikke. Trykk ▶ igjen.")),
        pause: () => el.pause(),
        seek: (t) => { el.currentTime = t; },
        time: () => el.currentTime,
        duration: () => (Number.isFinite(el.duration) ? el.duration : 0),
        playing: () => !el.paused,
      });
    } else {
      say("Laster …");
      const ERRORS = {
        2: "Ugyldig YouTube-ID.",
        5: "YouTube-videoen kan ikke spilles i nettleseren.",
        100: "YouTube-videoen finnes ikke eller er privat.",
        101: "Eieren tillater ikke at denne videoen spilles utenfor YouTube.",
        150: "Eieren tillater ikke at denne videoen spilles utenfor YouTube.",
      };
      // The player must exist in the page to play, so it is made invisible instead:
      // the embed shows the video title (often the answer) and must never be seen.
      const holder = document.createElement("div");
      holder.className = "music-yt";
      holder.setAttribute("aria-hidden", "true");
      holder.appendChild(document.createElement("div"));
      music.appendChild(holder);

      const create = () => {
        let yt;
        let checkStart = null;
        const state = () => (yt && yt.getPlayerState ? yt.getPlayerState() : -1);
        yt = new YT.Player(holder.firstElementChild, {
          host: "https://www.youtube-nocookie.com",
          width: 200,
          height: 200,
          videoId: music.dataset.youtube,
          playerVars: {
            start: Math.floor(start), controls: 0, disablekb: 1, fs: 0, rel: 0,
            playsinline: 1, iv_load_policy: 3, origin: location.origin,
          },
          events: {
            onReady: () => ready({
              play: () => {
                yt.playVideo();
                // Browsers may block playback; tell the host instead of failing silently.
                clearTimeout(checkStart);
                checkStart = setTimeout(() => {
                  if (![YT.PlayerState.PLAYING, YT.PlayerState.BUFFERING].includes(state())) {
                    say("Starter ikke? Trykk ▶ igjen.");
                  }
                }, 4000);
              },
              pause: () => yt.pauseVideo(),
              seek: (t) => yt.seekTo(t, true),
              time: () => yt.getCurrentTime() || 0,
              duration: () => yt.getDuration() || 0,
              playing: () => [YT.PlayerState.PLAYING, YT.PlayerState.BUFFERING].includes(state()),
            }),
            onStateChange: paint,
            onError: (e) => {
              clearTimeout(checkStart);
              say(ERRORS[e.data] || `YouTube-feil ${e.data}.`);
            },
          },
        });
      };

      if (window.YT && window.YT.Player) {
        create();
      } else {
        const previous = window.onYouTubeIframeAPIReady;
        window.onYouTubeIframeAPIReady = () => { if (previous) previous(); create(); };
        const script = document.createElement("script");
        script.src = "https://www.youtube.com/iframe_api";
        script.onerror = () => say("Fikk ikke kontakt med YouTube.");
        document.head.appendChild(script);
      }
    }
  }

  // --- Judge buttons: no double submits, a little haptic feedback -----------
  document.addEventListener("submit", (e) => {
    const form = e.target;
    if (form.dataset.sent) { e.preventDefault(); return; }
    form.dataset.sent = "1";
    const result = e.submitter && e.submitter.value;
    if (navigator.vibrate) navigator.vibrate(result === "wrong" ? [40, 60, 40] : 30);
  });

  // --- Upload page: suggest the quiz name from the file's own title ----------
  // Only fills the name while the host hasn't typed one (or it is still our last suggestion).
  const quizFile = document.querySelector(".upload-form input[type=file]");
  const quizName = document.querySelector(".upload-form input[name=name]");
  if (quizFile && quizName) {
    let suggested = "";
    quizFile.addEventListener("change", async () => {
      const file = quizFile.files[0];
      if (!file || (quizName.value.trim() && quizName.value !== suggested)) return;
      try {
        const title = JSON.parse(await file.text()).title;
        if (typeof title === "string" && title.trim()) quizName.value = suggested = title.trim().slice(0, 100);
      } catch (_) { /* not JSON: the server explains what is wrong on upload */ }
    });
  }

  // --- Sound: drumroll + fanfare, synthesized with the Web Audio API ---------
  // No audio files needed. Browsers only allow audio after a user gesture, so
  // the context is created lazily inside the host's tap.
  const Sound = (() => {
    const KEY = "kviss-muted";
    let muted = false;
    try { muted = localStorage.getItem(KEY) === "1"; } catch (_) {}
    let ctx = null, master = null, noise = null;

    function audio() {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      if (!ctx) {
        // iPhone: play even when the ring/silent switch is on silent (Safari 16.4+).
        try { if (navigator.audioSession) navigator.audioSession.type = "playback"; } catch (_) {}
        ctx = new AC();
        const comp = ctx.createDynamicsCompressor();
        master = ctx.createGain();
        master.gain.value = muted ? 0 : 0.7;
        master.connect(comp).connect(ctx.destination);
        noise = ctx.createBuffer(1, ctx.sampleRate, ctx.sampleRate);
        const d = noise.getChannelData(0);
        for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
      }
      if (ctx.state === "suspended") ctx.resume();
      return ctx;
    }

    function burst(t, { gain, decay, type, freq, q = 1 }) {
      const src = ctx.createBufferSource();
      src.buffer = noise;
      const f = ctx.createBiquadFilter();
      f.type = type; f.frequency.value = freq; f.Q.value = q;
      const g = ctx.createGain();
      g.gain.setValueAtTime(gain, t);
      g.gain.exponentialRampToValueAtTime(0.001, t + decay);
      src.connect(f).connect(g).connect(master);
      src.start(t, Math.random() * 0.5);
      src.stop(t + decay + 0.05);
    }

    // One brassy note: two detuned sawtooths through a lowpass filter that "opens" on attack.
    function brass(freq, t, dur, vol) {
      const g = ctx.createGain();
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(vol, t + 0.03);
      g.gain.setValueAtTime(vol, t + dur - 0.08);
      g.gain.exponentialRampToValueAtTime(0.0001, t + dur + 0.25);
      const f = ctx.createBiquadFilter();
      f.type = "lowpass";
      f.frequency.setValueAtTime(700, t);
      f.frequency.exponentialRampToValueAtTime(3200, t + 0.06);
      f.frequency.exponentialRampToValueAtTime(1600, t + dur);
      f.connect(g).connect(master);
      for (const detune of [-7, 7]) {
        const o = ctx.createOscillator();
        o.type = "sawtooth";
        o.frequency.value = freq;
        o.detune.value = detune;
        o.connect(f);
        o.start(t);
        o.stop(t + dur + 0.3);
      }
    }

    return {
      get muted() { return muted; },
      setMuted(value) {
        muted = value;
        try { localStorage.setItem(KEY, value ? "1" : "0"); } catch (_) {}
        if (master) master.gain.setTargetAtTime(value ? 0 : 0.7, ctx.currentTime, 0.02);
      },
      // Snare roll that swells in volume and speed; returns false if it could not play.
      drumroll(seconds) {
        if (muted || !audio()) return false;
        const t0 = ctx.currentTime + 0.05;
        for (let t = 0; t < seconds; ) {
          const p = t / seconds;
          burst(t0 + t, { gain: 0.45 + 1.1 * p * p, decay: 0.08, type: "bandpass", freq: 1800, q: 0.9 });
          if (Math.round(t / 0.05) % 4 === 0) {
            burst(t0 + t, { gain: 0.5 + 0.8 * p, decay: 0.12, type: "lowpass", freq: 180 }); // tom rumble
          }
          t += 0.055 - 0.015 * p; // speeds up towards the end
        }
        return true;
      },
      // Cymbal crash + "ta-ta-ta-taaa" fanfare ending on a C major chord.
      fanfare() {
        if (muted || !audio()) return;
        const t = ctx.currentTime + 0.02;
        burst(t, { gain: 0.9, decay: 1.8, type: "highpass", freq: 5000 });
        burst(t, { gain: 0.9, decay: 0.35, type: "lowpass", freq: 120 }); // bass drum
        const G4 = 392, C5 = 523.25, E5 = 659.25, G5 = 783.99, C4 = 261.63;
        brass(G4, t, 0.13, 0.22);
        brass(G4, t + 0.16, 0.13, 0.22);
        brass(G4, t + 0.32, 0.13, 0.22);
        for (const f of [C4, C5, E5, G5]) brass(f, t + 0.48, 1.4, 0.16);
      },
    };
  })();

  // Mute toggle (only shown where there is sound to mute).
  const soundBtn = document.querySelector(".sound-toggle");
  if (soundBtn && document.querySelector(".final")) {
    const paint = () => {
      soundBtn.textContent = Sound.muted ? "🔇" : "🔊";
      soundBtn.setAttribute("aria-pressed", String(Sound.muted));
      soundBtn.setAttribute("aria-label", Sound.muted ? "Slå på lyd" : "Slå av lyd");
    };
    soundBtn.hidden = false;
    paint();
    soundBtn.addEventListener("click", () => { Sound.setMuted(!Sound.muted); paint(); });
  }

  // --- Host view: follow the TV live -----------------------------------------
  // Polls a small endpoint; the server answers 204 when nothing changed, so
  // idle polling costs next to nothing.
  const hostPanel = document.getElementById("host-panel");
  if (hostPanel) {
    const live = document.querySelector("[data-live]");
    const answerText = () => (hostPanel.querySelector(".host-answer") || {}).textContent || "";
    const setLive = (ok) => {
      live.classList.toggle("offline", !ok);
      live.textContent = ok ? "● Live" : "● Frakoblet";
    };
    const poll = async () => {
      const panel = hostPanel.querySelector(".host-panel");
      const v = panel ? panel.dataset.version : "";
      try {
        const res = await fetch(`${hostPanel.dataset.poll}?v=${encodeURIComponent(v)}`, { cache: "no-store" });
        if (res.status === 200) {
          const before = answerText();
          hostPanel.innerHTML = await res.text();
          const answer = hostPanel.querySelector(".host-answer");
          if (answer && answer.textContent !== before) {
            answer.classList.add("pop-in");
            if (navigator.vibrate) navigator.vibrate(40); // new question on the TV
          }
          setLive(true);
        } else {
          setLive(res.status === 204);
        }
      } catch (_) {
        setLive(false);
      }
      setTimeout(poll, document.hidden ? 5000 : 1500);
    };
    setTimeout(poll, 1500);
  }

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

    const DRUMROLL_SECONDS = 2.6;
    let busy = false; // ignore taps while the drumroll plays

    const reveal = (step) => {
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

    const advance = () => {
      if (busy || shown >= order.length) return;
      const step = order[shown++];
      const isWinner = shown === order.length;
      if (isWinner && Sound.drumroll(DRUMROLL_SECONDS)) {
        busy = true;
        next.textContent = "🥁 …";
        page.classList.add("drumroll");
        setTimeout(() => {
          busy = false;
          page.classList.remove("drumroll");
          Sound.fanfare();
          reveal(step);
        }, DRUMROLL_SECONDS * 1000);
      } else {
        if (isWinner) Sound.fanfare();
        reveal(step);
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
