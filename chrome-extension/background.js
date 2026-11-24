// Forwards claude.ai tab state to the Claude Taskbar app, and delivers replies typed
// in the panel back into the right tab.
//
// Uses Chrome native messaging (com.claudetaskbar.bridge): Chrome starts the app's
// host program for this extension only, with no network port, so other programs,
// other extensions and other accounts on the Mac can't read or forge these messages.
const HOST = "com.claudetaskbar.bridge";
let port = null;
let retryAt = 0;

function connect() {
  if (port || Date.now() < retryAt) return port;
  try {
    port = chrome.runtime.connectNative(HOST);
  } catch (e) {
    port = null;
    retryAt = Date.now() + 5000;
    return null;
  }
  port.onMessage.addListener((msg) => {
    if (msg?.type !== "reply" || typeof msg.id !== "string") return;
    const tabId = Number(msg.id.slice(3));
    chrome.tabs.sendMessage(tabId, { type: "claude-reply", text: msg.text }).catch(() => {});
  });
  port.onDisconnect.addListener(() => {
    void chrome.runtime.lastError; // app not installed or quit: try again later
    port = null;
    retryAt = Date.now() + 5000;
  });
  return port;
}

function post(msg) {
  const p = connect();
  if (!p) return;
  try {
    p.postMessage(msg);
  } catch (e) {
    port = null;
  }
}

// Content scripts report every few seconds, which also keeps this worker running.
chrome.runtime.onMessage.addListener((msg, sender) => {
  if (msg?.type !== "claude-task" || !sender.tab) return;
  post({ type: "tab", id: `tab${sender.tab.id}`, state: msg.state, title: msg.title, url: msg.url, last: msg.last });
});

chrome.tabs.onRemoved.addListener((tabId) => post({ type: "close", id: `tab${tabId}` }));

connect();
