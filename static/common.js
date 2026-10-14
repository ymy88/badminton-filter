// common.js —— 两个页面共用：DOM 取元素、请求、转义、平台名、拼音缓存
export const $ = (id) => document.getElementById(id);
export const SRC = { zhongyulian: "中羽联", flashx: "闪动" };
export const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// GET（无 body）或 POST JSON；非 2xx 抛错，错误信息优先取后端返回的 msg
export async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) {
    let msg = "";
    try { msg = (await r.json()).msg || ""; } catch (e) { /* 非 JSON 响应 */ }
    throw new Error(msg || `${path} ${r.status}`);
  }
  return r.json();
}

export const PY = {};  // 文本 → [全拼, 首字母]

// 给文本补拼音（只查缓存里没有的）；查不到只影响拼音匹配
export async function ensurePinyin(texts) {
  const need = [...new Set(texts)].filter((t) => t && !(t in PY));
  if (!need.length) return;
  try { Object.assign(PY, await api("/api/pinyin", { texts: need })); } catch (e) { /* 忽略 */ }
}
