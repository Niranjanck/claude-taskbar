// Forwards claude.ai tab state to the local bridge, and delivers replies typed
// in the Claude Taskbar panel back into the right tab.
const BRIDGE = "http://127.0.0.1:47823";
const HEADERS = { "Content-Type": "application/json", "X-Claude-Taskbar": "1" };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function post(path, body) {
  fetch(BRIDGE + path, { method: "POST", headers: HEADERS, body: JSON.stringify(body) })
    .catch(() => {}); // bridge not running: nothing to do
}

// Long-poll the bridge for replies. Content scripts message this worker every
// few seconds, which keeps it alive and restarts the loop if Chrome stopped it.
let polling = false;
async function pollReplies() {
  if (polling) return;
  polling = true;
  try {
    for (;;) {
      let res;
      try {
        res = await fetch(BRIDGE + "/wait", { headers: HEADERS });
      } catch (e) {
        await sleep(3000);
        continue;
      }
      if (res.status === 200) {
        for (const msg of await res.json()) {
          const tabId = Number(msg.id.slice(3));
          chrome.tabs.sendMessage(tabId, { type: "claude-reply", text: msg.text }).catch(() => {});
        }
      } else if (res.status !== 204) {
        await sleep(3000);
      }
    }
  } finally {
    polling = false;
  }
}

chrome.runtime.onMessage.addListener((msg, sender) => {
  pollReplies();
  if (msg?.type !== "claude-task" || !sender.tab) return;
  post("/tab", { id: `tab${sender.tab.id}`, state: msg.state, title: msg.title, url: msg.url, last: msg.last });
});

chrome.tabs.onRemoved.addListener((tabId) => post("/close", { id: `tab${tabId}` }));

pollReplies();
