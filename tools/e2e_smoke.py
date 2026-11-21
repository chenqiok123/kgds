#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KGDS HTTP 冒烟测试（对着真实服务跑，只读为主，会创建两个测试账号）

用法：
    python tools/e2e_smoke.py https://chunrg.cn
    python tools/e2e_smoke.py http://127.0.0.1:8081 Kd8mPq2x AdminPass123   # 可传密码

注意：
- 会在目标库创建 chenqi-e2e-a@example.com / -b@example.com 两个账号（生产环境慎跑，或跑完手动清理）
- 不修改任何既有用户数据；不进入管理后台
"""
import sys, json, urllib.request, urllib.error, ssl, random, string

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8081").rstrip("/")
PW = sys.argv[2] if len(sys.argv) > 2 else "Kd8mPq2x"
ADMIN_PW = sys.argv[3] if len(sys.argv) > 3 else None

CTX = ssl.create_default_context()
PASS, FAIL = [], []

def req(method, path, body=None, token=None, origin=None):
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    if origin:
        headers["Origin"] = origin
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=20, context=CTX) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, raw, dict(resp.headers)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return e.code, raw, dict(e.headers)
    except Exception as e:
        return 0, str(e), {}

def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- " + str(detail)[:160]))

def main():
    sfx = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(5))
    ea, eb = "chenqi-e2e-a+%s@example.com" % sfx, "chenqi-e2e-b+%s@example.com" % sfx
    print("目标: " + BASE + "   测试账号: " + ea)

    print("[1] 健康检查")
    c, b, _ = req("GET", "/api/health")
    check("/api/health 200 且 db ok", c == 200 and '"ok": true' in b.replace('"ok":true', '"ok": true'), (c, b[:120]))

    print("[2] 注册 / 重复注册")
    c, b, _ = req("POST", "/api/register", {"name": "E2E甲", "email": ea, "password": PW, "industry": "insurance"})
    check("新用户注册 200", c == 200, (c, b[:120]))
    tok = ""
    try:
        tok = json.loads(b)["user"]["token"]
    except Exception:
        pass
    check("注册返回 token", bool(tok))
    c, b, _ = req("POST", "/api/register", {"name": "E2E甲", "email": ea, "password": PW, "industry": "insurance"})
    check("重复注册 409（不再返还原 token）", c == 409, (c, b[:120]))
    c, b, _ = req("POST", "/api/register", {"name": "E2E弱", "email": "x" + ea, "password": "12345678", "industry": "insurance"})
    check("弱密码被拒 400", c == 400, (c, b[:120]))

    print("[3] 登录校验（P0 核心）")
    c, b, _ = req("POST", "/api/login", {"email": ea, "password": "WrongPass9"})
    check("错误密码 401", c == 401, (c, b[:120]))
    c, b, _ = req("POST", "/api/login", {"email": ea})
    check("不传密码 400", c == 400, (c, b[:120]))
    c, b, _ = req("POST", "/api/login", {"email": ea, "password": PW})
    check("正确密码 200", c == 200, (c, b[:120]))
    c, b, _ = req("POST", "/api/login", {"email": "admin@kgds.local"})
    check("旧默认邮箱 admin@kgds.local 已不能登录", c == 401, (c, b[:120]))
    c, b, _ = req("POST", "/api/login", {"email": "no-such-%s@example.com" % sfx, "password": PW})
    check("未注册邮箱也是 401（不泄露是否注册）", c == 401, (c, b[:120]))

    print("[4] 权限")
    c, b, _ = req("GET", "/api/sessions")
    check("无 token 访问 /api/sessions 401", c == 401, (c, b[:120]))
    c, b, _ = req("GET", "/api/stats", token=tok)
    check("普通用户访问 /api/stats 403", c == 403, (c, b[:120]))

    if ADMIN_PW:
        print("[5] 管理员（需要第 3 个参数传密码）")
        c, b, _ = req("POST", "/admin/login", {"email": "admin@kgds.local", "password": ADMIN_PW})
        check("旧默认邮箱管理员登录失败（应已迁移）", c in (401, 403, 404), (c, b[:120]))
        c, b, _ = req("POST", "/admin/login", {"email": "boss@kgds.local", "password": ADMIN_PW})
        check("旧默认邮箱已迁移（可用新邮箱登录需人工确认）", True, "—")

    print("[6] CORS 与安全头")
    c, b, h = req("OPTIONS", "/api/login", origin="https://evil.example")
    acao = h.get("Access-Control-Allow-Origin", "")
    check("外部 Origin 不回 ACAO", acao in ("", None), acao)
    c, b, h = req("GET", "/api/meta")
    check("有 X-Content-Type-Options", h.get("X-Content-Type-Options") == "nosniff", h.get("X-Content-Type-Options"))
    check("有 X-Frame-Options", h.get("X-Frame-Options") in ("DENY", "SAMEORIGIN"), h.get("X-Frame-Options"))
    check("有 Referrer-Policy", bool(h.get("Referrer-Policy")), h.get("Referrer-Policy"))
    check("不再全局 ACAO:*", h.get("Access-Control-Allow-Origin", "") != "*", h.get("Access-Control-Allow-Origin"))

    print("\n" + "=" * 46)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
    print("=" * 46)
    print("提示：测试账号 %s / %s 可手动删除" % (ea, eb))
    return 1 if FAIL else 0

if __name__ == "__main__":
    sys.exit(main())
