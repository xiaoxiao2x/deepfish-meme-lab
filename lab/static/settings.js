// 设置页的页面脚本：四组输入各自往 /api/settings/* 送一次。
// 判断都在后端 —— 地址怎么拆、模板名认不认、写回去的 TOML 合不合法，这里一概不管；
// 这里只把服务端回的那句 message 摆到人眼前，成功以后拿回来的值重塑页面。

(function () {
  "use strict";

  // 前缀与两个请求方法只有一处实现：api.js 先加载，这里照用
  const { get, post } = window.LabApi;

  const $ = (id) => document.getElementById(id);

  function say(node, text, bad) {
    node.textContent = text;
    node.classList.toggle("bad", Boolean(bad));
  }

  function payloadOf(form) {
    const data = {};
    for (const field of form.elements) {
      if (field.name) data[field.name] = field.value;
    }
    return data;
  }

  // 三张表单是同一件事：读输入 → 送出去 → 说结果。主题那张不走这里（选完就换）。
  function wire(form, node, path, done) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = form.querySelector("button[type=submit]");
      button.disabled = true;
      say(node, "正在保存……", false);
      try {
        done(await post(path, payloadOf(form)));
      } catch (error) {
        say(node, error.message, true);
      } finally {
        button.disabled = false;
      }
    });
  }

  // ---------- 引擎地址：存下来一份，另外能当场试一次连接 ----------

  const engineSay = $("engine-say");
  const engineAddress = $("engine-address");

  wire($("engine-form"), engineSay, "/settings/engine", (saved) => {
    engineAddress.value = saved.address;
    say(engineSay, "地址记下了 —— " + saved.address + "。", false);
  });

  $("engine-test").addEventListener("click", async () => {
    say(engineSay, "正在连……", false);
    try {
      const result = await get("/settings/probe", { address: engineAddress.value });
      engineAddress.value = result.address;
      say(engineSay, result.detail, !result.ok);
    } catch (error) {
      say(engineSay, error.message, true);
    }
  });

  // ---------- 默认工作流 ----------

  wire($("workflow-form"), $("workflow-say"), "/settings/workflow", (saved) => {
    say($("workflow-say"), "默认工作流换成了 " + saved.name + "。", false);
  });

  // ---------- 路径：素材根目录按行拆开；存完服务端顺手重扫索引 ----------

  wire($("paths-form"), $("paths-say"), "/settings/paths", (saved) => {
    $("asset-roots").value = saved.asset_roots.join("\n");
    $("output-dir").value = saved.output_dir;
    say(
      $("paths-say"),
      saved.warning || "路径记下了：素材根目录 " + saved.asset_roots.length + " 个。",
      Boolean(saved.warning)
    );
  });

  // ---------- 主题：选完就换，不用点保存 ----------

  const themeSelect = $("theme");
  const themeSay = $("theme-say");

  themeSelect.addEventListener("change", async () => {
    try {
      const saved = await post("/settings/theme", { theme: themeSelect.value });
      document.documentElement.dataset.theme = saved.theme;
      themeSelect.value = saved.theme;
      say(themeSay, "主题换成" + themeSelect.selectedOptions[0].text + "了。", false);
    } catch (error) {
      say(themeSay, error.message, true);
    }
  });
})();
