// 出片台的页面脚本：原生 JS + EventSource，没有构建步骤（design.md §9.1）。
// 它只做两件事：把页面上的动作发出去、把服务端推回来的东西摆出来。
// 判断都在后端 —— 参数怎么翻译、账怎么记、形象钉不钉得住，这里一概不管。

(function () {
  "use strict";

  // 前缀只有一处来源：后端把常量注进 body 的 data-*，这里照读
  const API = document.body.dataset.api;
  const IMAGES = document.body.dataset.images;

  const $ = (id) => document.getElementById(id);
  const cards = new Map(); // 相对路径 → 那张卡片
  const pictures = []; // 本次出过的图，按出来顺序

  function post(path, payload) {
    return fetch(API + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(async (response) => {
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.message || "服务端没收下这件事");
      return body;
    });
  }

  function say(text, bad) {
    const line = $("progress");
    line.textContent = text;
    line.classList.toggle("bad", Boolean(bad));
  }

  // ---------- 引擎状态：一条长连接，页面自己不轮询 ----------

  const lamp = $("engine-lamp");
  const engineText = $("engine-text");
  const engineQueue = $("engine-queue");

  new EventSource(API + "/engine/events").onmessage = (message) => {
    const state = JSON.parse(message.data);
    lamp.classList.toggle("on", state.ok);
    engineText.textContent = state.ok ? "引擎在，" + state.version : state.message;
    engineQueue.textContent = state.ok
      ? "在算 " + state.running + " 张 · 排着 " + state.pending + " 张"
      : "";
  };

  // ---------- 底图：拖进来、或者点一下挑一张 ----------

  let base = null; // 选中的底图（相对 output 的路径）

  const drop = $("drop");
  const baseOn = $("base-on");
  const baseName = $("base-name");
  const basePreview = $("base-preview");
  const denoise = $("denoise");

  function useBase(relative) {
    base = relative;
    drop.hidden = true;
    baseOn.hidden = false;
    baseName.textContent = relative;
    basePreview.src = IMAGES + "/" + relative;
  }

  function dropBase() {
    base = null;
    drop.hidden = false;
    baseOn.hidden = true;
    basePreview.removeAttribute("src");
  }

  denoise.addEventListener("input", () => {
    $("denoise-out").textContent = denoise.value;
  });
  $("base-clear").addEventListener("click", dropBase);

  async function sendBaseFile(file) {
    if (!file) return;
    say("把这张底图收进来……");
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const data = String(reader.result).split(",", 2)[1];
        const answer = await post("/base", { name: file.name, data: data });
        useBase(answer.image);
        say("底图收到：" + answer.image);
      } catch (error) {
        say(error.message, true);
      }
    };
    reader.readAsDataURL(file);
  }

  drop.addEventListener("click", () => $("base-file").click());
  drop.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") $("base-file").click();
  });
  $("base-file").addEventListener("change", (event) =>
    sendBaseFile(event.target.files[0])
  );
  ["dragenter", "dragover"].forEach((name) =>
    drop.addEventListener(name, (event) => {
      event.preventDefault();
      drop.classList.add("over");
    })
  );
  ["dragleave", "drop"].forEach((name) =>
    drop.addEventListener(name, () => drop.classList.remove("over"))
  );
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    sendBaseFile(event.dataTransfer.files[0]);
  });

  // ---------- 出片：一张卡片一张图 ----------

  function addCard(relative, seed, id, spec) {
    const card = document.createElement("div");
    card.className = "card";
    card.dataset.file = relative;
    card.dataset.seed = String(seed);
    // 这张是怎么来的：重跑那张照它自己这份参数，跟此刻表单里写着什么无关
    card.dataset.prompt = spec.prompt;
    card.dataset.base = spec.base;
    card.dataset.denoise = spec.denoise === null ? "" : String(spec.denoise);
    card.innerHTML =
      '<img class="thumb" alt="' +
      id +
      '">' +
      '<p class="line"><strong>' +
      id +
      "</strong> · 种子 " +
      seed +
      "</p>" +
      '<div class="acts">' +
      '<button type="button" data-act="sticker">去背</button>' +
      '<button type="button" data-act="again">重跑这张</button>' +
      '<button type="button" data-act="keep">保留</button>' +
      "</div>";
    card.querySelector("img").src = IMAGES + "/" + relative;
    card.querySelector('[data-act="sticker"]').addEventListener("click", () =>
      makeSticker(card)
    );
    card.querySelector('[data-act="again"]').addEventListener("click", () =>
      runAgain(card)
    );
    card.querySelector('[data-act="keep"]').addEventListener("click", () =>
      keepIt(card)
    );
    $("grid").append(card);
    cards.set(relative, card);
    pictures.push(relative);
    return card;
  }

  function watchJob(jobId) {
    const source = new EventSource(API + "/jobs/" + jobId + "/events");
    let total = 0;
    let done = 0;
    let spec = { prompt: "", base: "", denoise: null };
    source.onmessage = (message) => {
      const event = JSON.parse(message.data);
      if (event.type === "spec") {
        spec = { prompt: event.prompt, base: event.base, denoise: event.denoise };
      } else if (event.type === "start") {
        total = event.total;
        say(done + "/" + total + " 已完成 · 第 " + event.index + " 张开始算（种子 " + event.seed + "）");
      } else if (event.type === "waiting") {
        say(
          done +
            "/" +
            total +
            " 已完成 · 第 " +
            event.index +
            " 张还在算 · 已等 " +
            event.elapsed +
            " 秒"
        );
      } else if (event.type === "done") {
        done = event.index;
        addCard(event.file, event.seed, event.id, spec);
        say(done + "/" + total + " 已完成");
      } else if (event.type === "finished") {
        source.close();
        say(event.made + " 张都在下面了 —— 挑一张去背、保留。");
      } else if (event.type === "failed") {
        source.close();
        say(event.message, true);
      }
    };
  }

  $("knobs").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    const seed = String(form.get("seed") || "").trim();
    const payload = {
      prompt: form.get("prompt"),
      count: Number(form.get("count")),
      seed: seed ? Number(seed) : null,
      init_image: base,
      denoise: base ? Number(denoise.value) : null,
    };
    try {
      const answer = await post("/make", payload);
      say("已经交给引擎了……");
      watchJob(answer.job);
    } catch (error) {
      say(error.message, true);
    }
  });

  // ---------- 卡片上的三个动作 ----------

  async function makeSticker(card) {
    try {
      const answer = await post("/sticker", { image: card.dataset.file });
      card.querySelector("img").src = IMAGES + "/" + answer.file;
      card.classList.add("checker");
      card.dataset.file = answer.file;
      say(answer.id + " 去背好了（底色是透明的）。");
    } catch (error) {
      say(error.message, true);
    }
  }

  async function runAgain(card) {
    // 照这一张自己的参数重跑：台词、种子、底图都是它当时那份
    const payload = {
      prompt: card.dataset.prompt,
      count: 1,
      seed: Number(card.dataset.seed),
      init_image: card.dataset.base || null,
      denoise: card.dataset.base ? Number(card.dataset.denoise) : null,
    };
    try {
      const answer = await post("/make", payload);
      say("照这一张自己的参数再出一张……");
      watchJob(answer.job);
    } catch (error) {
      say(error.message, true);
    }
  }

  async function keepIt(card) {
    try {
      const answer = await post("/keep", { image: card.dataset.file });
      card.querySelector('[data-act="keep"]').textContent = "已留";
      say(answer.id + " 进了配方库 —— 以后能照它重跑。");
    } catch (error) {
      say(error.message, true);
    }
  }

  $("sticker-all").addEventListener("click", async () => {
    for (const relative of pictures.slice()) {
      const card = cards.get(relative);
      if (card && !card.classList.contains("checker")) await makeSticker(card);
    }
  });

  $("open-folder").addEventListener("click", async () => {
    const last = pictures[pictures.length - 1];
    if (!last) {
      say("这次还没出片呢。", true);
      return;
    }
    try {
      const answer = await post("/open-folder", { image: last });
      say(answer.opened ? "文件夹弹出来了：" + answer.folder : answer.message);
    } catch (error) {
      say(error.message, true);
    }
  });

  $("clear").addEventListener("click", () => {
    $("grid").innerHTML = "";
    cards.clear();
    pictures.length = 0;
    say("清干净了 —— 图还在当天的文件夹里，没删。");
  });
})();
