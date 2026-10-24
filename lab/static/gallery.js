/* 成品库那一页的交互：只有「打开文件夹」要问服务端，别的都是链接。 */
(() => {
  const { post } = window.LabApi;
  const status = document.getElementById("gallery-status");

  function say(text, bad) {
    if (!status) return;
    status.textContent = text;
    status.classList.toggle("bad", Boolean(bad));
  }

  document.querySelectorAll('button[data-act="open-folder"]').forEach((button) => {
    button.addEventListener("click", async () => {
      const card = button.closest(".card");
      if (!card) return;
      try {
        const answer = await post("/open-folder", { image: card.dataset.file });
        say(answer.message || "文件夹弹出去了：" + answer.folder, !answer.opened);
      } catch (error) {
        say(error.message, true);
      }
    });
  });
})();
