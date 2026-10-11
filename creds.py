# creds.py —— 两平台登录凭证的读写、失效检测、抓取
import json, os, shutil, subprocess, tempfile, time

PATH = "creds.json"

def load():
    try:
        with open(PATH) as f:
            d = json.load(f)
    except (FileNotFoundError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}

def save(data):
    # 原子创建 0600，避免先按 umask 建出宽权限文件的窗口
    fd = os.open(PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.chmod(PATH, 0o600)  # 兜底：旧文件可能是宽权限

def is_auth_failure(source, resp_text, status_code):
    if source == "flashx" and status_code == 401:
        return True
    try:
        d = json.loads(resp_text)
    except (ValueError, TypeError):
        return False  # 非 JSON（如中羽联城市为空的 Fatal error HTML）不是鉴权问题
    if not isinstance(d, dict):
        return False
    if source == "zhongyulian":
        return d.get("ok") == -2
    if source == "flashx":
        return d.get("code") in (401, 403)
    return False

# mitmproxy addon：只认两个域名，提取凭证 + 必要请求头，合并写入 creds.json
_ADDON = r'''
import json, os
PATH = os.environ["CREDS_PATH"]
KEEP = ("referer", "user-agent", "xweb_xhr", "x-version", "request-channel", "content-type")

def _merge(source, entry):
    try:
        with open(PATH) as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        data = {}
    data[source] = entry
    fd = os.open(PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.chmod(PATH, 0o600)

def request(flow):
    host = flow.request.pretty_host
    hdrs = {k: v for k, v in flow.request.headers.items() if k.lower() in KEEP}
    if host == "service2.zhongyulian.com":
        try:
            skey = json.loads(flow.request.get_text()).get("skey")
        except Exception:
            return
        if skey:
            _merge("zhongyulian", {"skey": skey, "headers": hdrs})
    elif host == "flash.flashxsport.com":
        token = flow.request.headers.get("x-access-token")
        if token:
            _merge("flashx", {"token": token, "headers": hdrs})
'''

def capture(timeout=90):
    """拉起 mitmdump 抓微信小程序流量，两平台凭证都到手（或超时）即停。需手测：
    运行后在微信里分别打开中羽联、闪动小程序并刷新列表。
    返回 load() 的结果；没抓到的平台沿用旧值（若有），调用方需自行检查两个平台的 key 是否都在。"""
    if not shutil.which("mitmdump"):
        raise RuntimeError("未找到 mitmdump，请先安装：brew install mitmproxy（或 uv tool install mitmproxy）")
    platforms = ("zhongyulian", "flashx")
    old = load()
    backup = {k: old[k] for k in platforms if k in old}
    # 抓取前清掉旧凭证，才能分辨"新抓到"；结束后对没抓到的回填旧值
    save({k: v for k, v in old.items() if k not in platforms})
    addon = None
    p = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            addon = f.name
            f.write(_ADDON)
        p = subprocess.Popen(
            ["mitmdump", "--mode", "local:WeChat", "-s", addon, "-q"],
            env={**os.environ, "CREDS_PATH": os.path.abspath(PATH)},
        )
        end = time.time() + timeout
        while time.time() < end:
            d = load()
            if all(k in d for k in platforms):
                break
            if p.poll() is not None:
                break
            time.sleep(1)
    finally:
        if p:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        if addon:
            os.unlink(addon)
        d = load()
        for k, v in backup.items():
            d.setdefault(k, v)
        save(d)
    return d
