"use strict";
const statusLabel = document.querySelector("#status");
const output = document.querySelector("#output");
const pair = document.querySelector("#pair");
const input = document.querySelector("#input");
const encoder = new TextEncoder();
let decoder = new TextDecoder();
let socket;
let terminal;
let paired = false;
let attempts = 0;
let retry;
function scheduleReconnect() {
  if (!paired || retry || !navigator.onLine || attempts >= 8) return;
  const delay = Math.min(15000, 750 * 2 ** attempts++);
  retry = setTimeout(() => {
    retry = undefined;
    connect();
  }, delay);
}
function reconnect() {
  clearTimeout(retry);
  retry = undefined;
  attempts = 0;
  connect();
}
if (window.Terminal) {
  terminal = new window.Terminal({
    cols: 80, rows: 24, fontSize: 13, scrollback: 1500,
    theme: {background: "#151b2a", foreground: "#e2e9fc"}
  });
  terminal.open(output);
  terminal.onData(send);
}
function send(data) {
  if (socket?.readyState !== WebSocket.OPEN) {
    statusLabel.textContent = "连接已断开";
    return false;
  }
  socket.send(encoder.encode(data));
  return true;
}
function resize() {
  if (!terminal || socket?.readyState !== WebSocket.OPEN) return;
  const cols = Math.max(20, Math.min(120, Math.floor((output.clientWidth - 20) / 8)));
  const rows = Math.max(5, Math.min(60, Math.floor((output.clientHeight - 16) / 17)));
  terminal.resize(cols, rows);
  socket.send("size " + cols + " " + rows);
}
function connect() {
  if (socket && socket.readyState < WebSocket.CLOSING) return;
  statusLabel.textContent = "正在连接";
  const current = new WebSocket("wss://" + location.host + "/terminal");
  socket = current;
  current.binaryType = "arraybuffer";
  current.onopen = () => {
    paired = true;
    attempts = 0;
    pair.hidden = true;
    statusLabel.textContent = terminal ? "终端已连接" : "已连接 · 简易文本模式";
    decoder = new TextDecoder();
    if (terminal) terminal.reset();
    else output.textContent = "";
    resize();
  };
  current.onmessage = ({data}) => {
    if (terminal) terminal.write(new Uint8Array(data));
    else {
      output.textContent = (output.textContent + decoder.decode(data, {stream: true})
        .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, "")).slice(-150000);
      output.scrollTop = output.scrollHeight;
    }
  };
  current.onclose = () => {
    if (socket === current) {
      statusLabel.textContent = paired && attempts < 8 ? "连接断开，等待重连" : "未连接";
      if (attempts >= 8) pair.hidden = false;
      scheduleReconnect();
    }
  };
}
pair.addEventListener("submit", async event => {
  event.preventDefault();
  try {
    const response = await fetch("/pair", {method: "POST", body: new URLSearchParams(new FormData(pair))});
    pair.reset();
    if (response.ok) {
      paired = true;
      reconnect();
    }
    else statusLabel.textContent = "配对失败";
  } catch {
    statusLabel.textContent = "配对失败";
  }
});
input.addEventListener("submit", event => {
  event.preventDefault();
  if (send(input.elements.message.value + "\r")) input.reset();
});
document.querySelector("#reconnect").onclick = reconnect;
document.querySelector("#interrupt").onclick = () => send("\x03");
document.querySelector("#escape").onclick = () => send("\x1b");
document.querySelector("#tab").onclick = () => send("\t");
window.addEventListener("resize", resize);
window.addEventListener("online", () => { if (paired) reconnect(); });
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && paired && socket?.readyState > WebSocket.OPEN) reconnect();
});
connect();
