// 各页共用的那一小截：接口前缀与两次请求（GET / POST）。
// 前缀只有一处来源：后端把常量注进 body 的 data-*，这里照读，页面里别写死 /api。
// 除了发请求，这里什么都不判断 —— 服务端不收的，把它的 message 原样抛出去。

(function () {
  "use strict";

  const API = document.body.dataset.api;

  async function get(path, params) {
    const query = params ? "?" + new URLSearchParams(params).toString() : "";
    const response = await fetch(API + path + query);
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.message || "服务端没收下这件事");
    return body;
  }

  async function post(path, payload) {
    const response = await fetch(API + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.message || "服务端没收下这件事");
    return body;
  }

  window.LabApi = { API, get, post };
})();
