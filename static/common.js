// common.js —— 两个页面共用：DOM 取元素、请求、转义、平台名、拼音缓存、选点区
import { parseCoord } from "/static/filters.js";

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

// 高德地图上看这个点（新标签页打开）
export const mapLink = (name, lat, lng) => lat == null ? "" :
  ` <a href="https://uri.amap.com/marker?position=${lng},${lat}&name=${encodeURIComponent(name)}" target="_blank" rel="noopener">地图</a>`;

// 手动坐标输入的问题说明；没问题返回 ""
export function coordError(text, ll) {
  if (/j\.map\.baidu\.com/.test(text)) return "百度短链接里没有坐标：先在浏览器打开它，等页面显示出场馆后，复制地址栏的完整链接";
  if (!ll && /baidu\.com/.test(text)) return "这个百度链接没定位到场馆：等页面显示出场馆详情、地址栏里出现 uid= 后再复制";
  if (!ll) return "格式不对，应为高德坐标「经度,纬度」或百度地图链接（地址栏里带 @ 的那种）";
  if (ll.coarse) return "精度不够：小数至少 4 位（坐标拾取器要登录并认证才显示完整坐标）";
  return "";
}

// 选点区（主页出发地、球馆管理页共用）：关键字搜高德 POI 列候选；搜不到就手动填高德坐标或百度地图链接。
// onPick(p)：点候选 p = 高德 POI {amap_id,name,lat,lng,district,address}；「用这个坐标」p = {lat,lng}（已是高德坐标）
// onInput(q)：关键字框每次输入时调。返回 {input: 关键字框, slot: 搜索框与高德候选之间的空容器}
export function locPicker(el, { query = "", onPick, onInput = () => {} }) {
  el.innerHTML = `<div class="lp">
    <div class="row"><input class="lp-q" type="text"><button type="button" class="lp-go">在高德搜索</button></div>
    <div class="lp-slot"></div><div class="pick lp-res"></div>
    <div class="row"><span class="nowrap">高德搜不到？手动指定坐标：</span>
      <input class="lp-ll" type="text" placeholder="高德坐标 或 百度地图链接">
      <button type="button" class="lp-use">用这个坐标</button></div>
    <div class="muted">在 <a href="https://map.baidu.com/" target="_blank" rel="noopener">百度地图</a> 点开地点（别拖动地图），把地址栏链接整条粘进来；
      或从 <a href="https://lbs.amap.com/tools/picker" target="_blank" rel="noopener">高德坐标拾取器</a> 复制坐标（要登录认证才有完整小数）<span class="lp-prev"></span></div></div>`;
  const q = el.querySelector(".lp-q"), ll = el.querySelector(".lp-ll");
  const res = el.querySelector(".lp-res"), prev = el.querySelector(".lp-prev");
  q.value = query;
  let pois = [];
  const search = async () => {
    const kw = q.value.trim();
    if (!kw) return;
    res.innerHTML = `<div class="head">高德搜索中…</div>`;
    try {
      ({ pois } = await api(`/api/amap/search?q=${encodeURIComponent(kw)}`));
      res.innerHTML = `<div class="head">高德搜索结果（${pois.length}）</div>` + (pois.map((p, i) =>
        `<div data-poi="${i}">${esc(p.name)}<small>${esc(p.district)} ${esc(p.address)}</small>${mapLink(p.name, p.lat, p.lng)}</div>`).join("")
        || `<div class="head">高德没有搜到，换个关键字试试，或在下面手动指定坐标</div>`);
    } catch (e) { res.innerHTML = `<div class="head err">高德搜索失败：${esc(e.message)}</div>`; }
  };
  q.addEventListener("input", () => onInput(q.value.trim()));
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") search(); });
  el.querySelector(".lp-go").onclick = search;
  res.onclick = (e) => {
    if (e.target.closest("a")) return;  // 地图链接照常打开
    const d = e.target.closest("[data-poi]");
    if (d) onPick(pois[Number(d.dataset.poi)]);
  };
  // 手动坐标：边输入边预览换算后的高德坐标，可点地图链接核对位置
  const check = () => { const t = ll.value, p = parseCoord(t); return [p, t.trim() ? coordError(t, p) : "先填高德坐标或百度地图链接"]; };
  ll.addEventListener("input", () => {
    const [p, err] = check();
    prev.innerHTML = !ll.value.trim() ? "" : err ? ` → <span class="err">${err}</span>`
      : ` → 高德坐标 ${p.lng},${p.lat}${mapLink("位置", p.lat, p.lng)}`;
  });
  el.querySelector(".lp-use").onclick = () => {
    const [p, err] = check();
    if (err) prev.innerHTML = ` → <span class="err">${err}</span>`;
    else onPick({ lat: p.lat, lng: p.lng });
  };
  return { input: q, slot: el.querySelector(".lp-slot") };
}
