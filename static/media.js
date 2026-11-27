// The audio files page (/lydfiler). Without this script the page is plain forms; with it, deleting asks
// first, and uploads go one file at a time to /lag/lyd with progress, then the list is refreshed in place.
(() => {
  "use strict";

  document.addEventListener("submit", (e) => {
    const message = e.target.dataset?.confirm;
    if (message && !confirm(message)) e.preventDefault();
  });

  const form = document.getElementById("media-upload");
  if (!form) return;
  const input = form.querySelector('input[type="file"]');
  const button = form.querySelector("button");
  const list = form.querySelector(".media-progress");
  const maxBytes = Number(form.dataset.maxMb) * 1024 * 1024;

  const size = (bytes) => (bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} kB`
    : `${(bytes / 1024 / 1024).toFixed(1).replace(".", ",")} MB`);

  function line(text, failed = false) {
    const li = document.createElement("li");
    li.textContent = text;
    li.classList.toggle("upload-failed", failed);
    list.append(li);
    return li;
  }

  function upload(file, li) {
    return new Promise((resolve) => {
      const say = (text, failed = false) => {
        li.textContent = text;
        li.classList.toggle("upload-failed", failed);
      };
      if (file.size > maxBytes) {
        say(`✗ ${file.name}: ${size(file.size)}, maks er ${form.dataset.maxMb} MB.`, true);
        return resolve(false);
      }
      const body = new FormData();
      body.append("file", file);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", form.dataset.uploadUrl);
      xhr.responseType = "json";
      xhr.upload.addEventListener("progress", (e) => {
        if (e.lengthComputable) say(`${file.name} … ${Math.round((e.loaded / e.total) * 100)} %`);
      });
      xhr.addEventListener("error", () => {
        say(`✗ ${file.name}: fikk ikke kontakt med serveren.`, true);
        resolve(false);
      });
      xhr.addEventListener("load", () => {
        const reply = xhr.response;
        if (xhr.status === 200 || xhr.status === 201) {
          say(`✓ ${file.name} → ${reply.file} (${reply.format}${reply.reused ? ", fantes fra før" : ""})`);
          resolve(true);
        } else {
          say(`✗ ${file.name}: ${reply?.error || `feil (HTTP ${xhr.status})`}`, true);
          resolve(false);
        }
      });
      say(`${file.name} …`);
      xhr.send(body);
    });
  }

  // The file list and totals as the server has them now.
  async function refresh() {
    try {
      // Not location: after a rename or delete the page is the answer to that post, at its own URL.
      const resp = await fetch(form.dataset.listUrl, { credentials: "same-origin" });
      const page = new DOMParser().parseFromString(await resp.text(), "text/html");
      const fresh = page.getElementById("media-files");
      if (resp.ok && fresh) document.getElementById("media-files").replaceWith(fresh);
    } catch (_) { /* the list is refreshed on the next load */ }
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const files = [...input.files];
    if (!files.length) return;
    button.disabled = true;
    input.disabled = true;
    list.replaceChildren();
    list.hidden = false;
    const lines = files.map((f) => line(`${f.name}: venter`));
    let saved = 0;
    for (const [i, file] of files.entries()) {
      if (await upload(file, lines[i])) saved += 1;
    }
    input.value = "";
    button.disabled = false;
    input.disabled = false;
    if (saved) await refresh();
  });
})();
