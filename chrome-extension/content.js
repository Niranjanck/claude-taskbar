// Watches a claude.ai tab: reports whether Claude is replying, finished, or idle,
// passes along the end of Claude's last reply, and types in replies sent from the
// Claude Taskbar panel.
//
// claude.ai's markup can change. If something stops working, update the selectors:
//   STOP_SELECTOR      the Stop button shown while a reply streams
//   REPLY_SELECTOR     Claude's reply blocks
//   COMPOSER_SELECTOR  the message box
//   SEND_SELECTOR      the send button
(() => {
  const STOP_SELECTOR = 'button[aria-label*="stop" i], [data-testid*="stop" i]';
  const REPLY_SELECTOR = '.font-claude-response, .font-claude-message, [data-testid="assistant-message"]';
  const COMPOSER_SELECTOR = 'div.ProseMirror[contenteditable="true"], [contenteditable="true"][role="textbox"]';
  const SEND_SELECTOR = 'button[aria-label*="send" i]';
  const HEARTBEAT_MS = 20000;

  let lastKey = "";
  let lastSent = 0;
  let wasRunning = false;
  let lastUrl = location.href;
  let pending = false;

  function lastReply() {
    const blocks = document.querySelectorAll(REPLY_SELECTOR);
    const el = blocks[blocks.length - 1];
    return el ? el.innerText.trim().slice(-1000) : "";
  }

  function snapshot() {
    if (location.href !== lastUrl) { // switched chats inside the same tab
      lastUrl = location.href;
      wasRunning = false;
    }
    const running = !!document.querySelector(STOP_SELECTOR);
    if (running) wasRunning = true;
    const state = running ? "running" : wasRunning ? "done" : "idle";
    const title = (document.title || "").replace(/\s*[-–|]\s*Claude\s*$/i, "").trim() || "New chat";
    return { state, title, url: location.href, last: state === "done" ? lastReply() : "" };
  }

  function report() {
    pending = false;
    const snap = snapshot();
    const key = JSON.stringify(snap);
    const now = Date.now();
    if (key === lastKey && now - lastSent < HEARTBEAT_MS) return;
    lastKey = key;
    lastSent = now;
    try {
      chrome.runtime.sendMessage({ type: "claude-task", ...snap });
    } catch (e) {
      // Extension was reloaded; this old content script can no longer talk to it.
    }
  }

  function schedule() {
    if (pending) return;
    pending = true;
    setTimeout(report, 400);
  }

  function sendReply(text) {
    const box = document.querySelector(COMPOSER_SELECTOR);
    if (!box) return false;
    box.focus();
    const range = document.createRange();
    range.selectNodeContents(box);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    document.execCommand("insertText", false, text);
    setTimeout(() => {
      const send = document.querySelector(SEND_SELECTOR);
      if (send && !send.disabled) {
        send.click();
      } else {
        box.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, bubbles: true }));
      }
    }, 200);
    return true;
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg?.type !== "claude-reply") return;
    sendResponse({ ok: sendReply(String(msg.text || "")) });
  });

  new MutationObserver(schedule).observe(document.documentElement, {
    subtree: true, childList: true, attributes: true, attributeFilter: ["aria-label"],
  });
  setInterval(schedule, 5000);
  report();
})();
