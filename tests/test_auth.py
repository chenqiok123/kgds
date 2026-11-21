#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KGDS 鉴权核心单元测试（2026-11-21 安全修复配套）

跑法（在仓库根目录）：
    python tests/test_auth.py

说明：
- 用临时目录做 KGDS_DATA_DIR，**不碰线上/本地真实库**
- 覆盖：口令哈希格式、旧哈希透明升级、注册/重复注册、密码校验、绑定密码、管理员登录、ADMIN_EMAIL 迁移、密码强度
- 不覆盖 HTTP 层（headers/CORS/限流），那部分用 tools/e2e_smoke.sh
"""
import os, sys, tempfile, shutil

TMP = tempfile.mkdtemp(prefix="kgds-test-")
os.environ["KGDS_DATA_DIR"] = TMP
os.environ["ADMIN_PASSWORD"] = "AdminPass123"
os.environ["ADMIN_EMAIL"] = "boss@example.com"
os.environ["PYTHONIOENCODING"] = "utf-8"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import server  # noqa: E402

PASS, FAIL = [], []

def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- " + str(detail)))

def main():
    print("[1] 初始化库")
    server.init_db()
    check("管理员按 ADMIN_EMAIL 建出", server.get_db().execute(
        "SELECT email FROM users WHERE is_admin=1").fetchone()["email"] == "boss@example.com")

    print("[2] 口令哈希")
    h = server.hash_pw("Kd8mPq2x")
    check("格式为 pbkdf2$iter$salt$hash", h.startswith("pbkdf2$"), h[:30])
    check("同密码两次哈希不同（有随机盐）", h != server.hash_pw("Kd8mPq2x"))
    check("正确密码校验通过", server.verify_pw("Kd8mPq2x", h))
    check("错误密码校验失败", not server.verify_pw("Kd8mPq2x5", h))
    legacy = __import__("hashlib").sha256(("kgds_salt_" + "OldPass123").encode()).hexdigest()
    check("旧格式哈希仍能校验（向后兼容）", server.verify_pw("OldPass123", legacy))
    check("is_legacy_hash 识别旧格式", server.is_legacy_hash(legacy) and not server.is_legacy_hash(h))

    print("[3] 注册")
    u = server.register_user("张三", "z@example.com", "Kd8mPq2x", "insurance", "", "民生人寿", "13800000001")
    check("注册成功并带 token", bool(u and u.get("token")))
    check("重复注册返回 None（不再返还原 token）", server.register_user("张三", "z@example.com", "Kd8mPq2x") is None)

    print("[4] 登录校验")
    check("密码正确可登录", server.verify_login("z@example.com", "Kd8mPq2x") is not None)
    check("密码错误不可登录", server.verify_login("z@example.com", "nope1234") is None)
    check("不存在的邮箱不可登录", server.verify_login("ghost@example.com", "Kd8mPq2x") is None)
    check("无密码账号不可登录", server.verify_login("boss@example.com", "") is None)

    print("[5] 旧哈希透明升级")
    server.register_user("李四", "l@example.com", "Kd8mPq2x")
    c = server.get_db()
    c.execute("UPDATE users SET password_hash=? WHERE email=?", (legacy, "l@example.com")); c.commit(); c.close()
    ok = server.verify_login("l@example.com", "OldPass123")
    newh = server.get_db().execute("SELECT password_hash FROM users WHERE email=?", ("l@example.com",)).fetchone()[0]
    check("旧哈希能登录", ok is not None)
    check("登录后哈希已升级为 pbkdf2", newh.startswith("pbkdf2$"), newh[:20])
    check("升级后原密码仍可登录（密码没变，只换了哈希）", server.verify_login("l@example.com", "OldPass123") is not None)
    check("升级后未设置过的密码不可登录", server.verify_login("l@example.com", "Kd8mPq2x") is None)

    print("[6] 绑定密码（方案 A）")
    server.register_user("王五", "w@example.com", "Kd8mPq2x", "insurance", "", "民生人寿", "13900000002")
    c = server.get_db(); c.execute("UPDATE users SET password_hash=NULL WHERE email=?", ("w@example.com",)); c.commit(); c.close()
    check("手机号不匹配被拒", server.bind_password("w@example.com", "王五", "13900009999", "NewPass123")[0] is False)
    check("姓名为空被拒", server.bind_password("w@example.com", "", "13900000002", "NewPass123")[0] is False)
    check("三要素匹配可绑定", server.bind_password("w@example.com", "王五", "13900000002", "NewPass123")[0] is True)
    check("绑定后新密码可登录", server.verify_login("w@example.com", "NewPass123") is not None)
    check("已设密码的账号不能重复绑定", server.bind_password("w@example.com", "王五", "13900000002", "Other123")[0] is False)
    check("无手机号的存量账号拒绝绑定（提示联系管理员）",
          server.bind_password("boss@example.com", "管理员", "", "NewPass123")[0] is False)

    print("[7] 管理员登录")
    check("正确密码可登录", server.admin_login("boss@example.com", "AdminPass123") is not None)
    check("错误密码返回 None", server.admin_login("boss@example.com", "wrong123") is None)
    check("普通用户走管理员入口返回 None", server.admin_login("z@example.com", "Kd8mPq2x") is None)
    check("管理员登录后 token 已轮换",
          server.admin_login("boss@example.com", "AdminPass123")["token"] !=
          server.admin_login("boss@example.com", "AdminPass123")["token"])

    print("[8] ADMIN_EMAIL 迁移")
    os.environ["ADMIN_EMAIL"] = "newboss@example.com"
    server.init_db()
    cur = server.get_db().execute("SELECT email FROM users WHERE is_admin=1").fetchone()["email"]
    check("改 ADMIN_EMAIL 后重新 init_db 即迁移", cur == "newboss@example.com", cur)
    check("迁移后旧密码仍可登录", server.admin_login("newboss@example.com", "AdminPass123") is not None)

    print("[9] 密码强度")
    for bad in ("", "1234567", "abcdefgh", "12345678"):
        check("拒绝弱密码: %r" % bad, server.check_password_strength(bad) is not None)
    check("接受 Kd8mPq2x", server.check_password_strength("Kd8mPq2x") is None)

    print("\n" + "=" * 46)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
    print("=" * 46)
    return 1 if FAIL else 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
