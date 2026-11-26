"""本地账号系统（反代 + 登录器体系第 1 步）

设计约定（与既有 users/players 表兼容）：
- 账号编号 uid 直接等于 players.player_id，一个账号一个角色；
- users 表继续用 id/username/password/user_token，新增 nickname/birthday/created_at/last_login/banned；
- password 存 pbkdf2_sha256$轮数$盐$哈希；兼容旧的明文记录（首登自动升级为哈希）；
- user_token 就是客户端 1001 里带的 login_token，也是管道 loginSuccess 里的 userToken。
"""

import hashlib
import os
import secrets
import time

import utils.db as db
from config import Config

_ITER = 200000
_UID_BASE = 10000


# --------------------------------------------------------------------------- #
# 密码
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITER)
    return f"pbkdf2_sha256${_ITER}${salt}${dk.hex()}"


def check_password(stored: str, password: str) -> bool:
    if not stored:
        return False
    if not stored.startswith("pbkdf2_sha256$"):
        # 旧记录是明文
        return stored == password
    try:
        _, it, salt, want = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(it))
        return secrets.compare_digest(dk.hex(), want)
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# 表结构
# --------------------------------------------------------------------------- #
_EXTRA_COLUMNS = {
    "nickname": "TEXT",
    "birthday": "TEXT",
    "created_at": "INTEGER",
    "last_login": "INTEGER",
    "banned": "INTEGER DEFAULT 0",
}


_schema_ready = False


def ensure_schema():
    """给既有 users 表补列；新库同样可用。进程内只做一次。"""
    global _schema_ready
    if _schema_ready:
        return
    rows = db.db.execute("PRAGMA table_info(users)").fetchall()
    have = {r[1] for r in rows}
    for col, typ in _EXTRA_COLUMNS.items():
        if col not in have:
            db.db.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")
    db.db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username ON users(username)"
    )
    db.save()
    _schema_ready = True


def _row(uid: int):
    return db.db.execute(
        "SELECT id, username, password, user_token, nickname, birthday, banned "
        "FROM users WHERE id = ?",
        (uid,),
    ).fetchone()


def _by_name(username: str):
    ensure_schema()
    return db.db.execute(
        "SELECT id, username, password, user_token, nickname, birthday, banned "
        "FROM users WHERE username = ?",
        (username,),
    ).fetchone()


def _next_uid() -> int:
    u = db.db.execute("SELECT IFNULL(MAX(id), 0) FROM users").fetchone()[0] or 0
    p = db.db.execute("SELECT IFNULL(MAX(player_id), 0) FROM players").fetchone()[0] or 0
    return max(int(u), int(p), _UID_BASE) + 1


def _apply_profile(uid: int, nickname: str = None, birthday: str = None):
    """把昵称/生日写进 players（客户端资料页读的是 players）。"""
    if nickname:
        db.set_players_info(uid, "player_name", nickname)
    if birthday:
        db.set_players_info(uid, "birthday", birthday)


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #
_NICK_MAX_BYTES = 48


def _check_nickname(nickname: str):
    """昵称合法性：1~48 字节、不能与已有玩家重名。返回 (ok, 规范值或错误)"""
    nickname = (nickname or "").strip()
    if not nickname:
        return False, "昵称不能为空"
    if len(nickname.encode("utf-8")) > _NICK_MAX_BYTES:
        return False, "昵称太长（最多 16 个汉字 / 48 字节）"
    if db.get_player_name_exists(nickname):
        return False, "昵称已被占用"
    return True, nickname


def register(username: str, password: str, nickname: str = None, birthday: str = "") -> dict:
    """注册并返回 {uid, username, nickname, userToken}。用户名唯一。"""
    username = (username or "").strip()
    if not username or not password:
        return {"ok": False, "error": "账号和密码不能为空"}
    if len(username.encode("utf-8")) > 48:
        return {"ok": False, "error": "账号过长"}
    if len(password) < 4:
        return {"ok": False, "error": "密码至少 4 位"}
    if _by_name(username) is not None:
        return {"ok": False, "error": "账号已存在"}

    # 昵称：不填就用账号名；必须唯一
    nick = (nickname or "").strip() or username
    ok_nick, val = _check_nickname(nick)
    if not ok_nick:
        return {"ok": False, "error": val}
    nick = val

    ensure_schema()
    uid = _next_uid()
    token = secrets.token_hex(16)
    now = int(time.time())
    db.db.execute(
        "INSERT INTO users (id, username, password, user_token, nickname, birthday, created_at, last_login, banned) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
        (uid, username, hash_password(password), token, nick, birthday or "", now, now),
    )
    db.save()

    # 建角色（1.19 万行 achieve，实测 57ms）
    db.get_player_id(uid)
    _apply_profile(uid, nick, birthday or "")
    db.save()

    return {
        "ok": True,
        "uid": uid,
        "username": username,
        "nickname": nick,
        "userToken": token,
    }


def login(username: str, password: str) -> dict:
    username = (username or "").strip()
    row = _by_name(username)
    if row is None:
        return {"ok": False, "error": "账号不存在"}
    uid, uname, stored, token, nickname, birthday, banned = row
    if banned:
        return {"ok": False, "error": "账号已被封禁"}
    if not check_password(stored, password):
        return {"ok": False, "error": "密码错误"}

    # 明文记录首登升级
    if not str(stored).startswith("pbkdf2_sha256$"):
        db.db.execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(password), uid))
    # 每次登录换 token（旧 token 立即失效）
    token = secrets.token_hex(16)
    db.db.execute(
        "UPDATE users SET user_token = ?, last_login = ? WHERE id = ?",
        (token, int(time.time()), uid),
    )
    db.save()
    db.get_player_id(uid)
    return {
        "ok": True,
        "uid": uid,
        "username": uname,
        "nickname": nickname or uname,
        "birthday": birthday or "",
        "userToken": token,
    }


def verify(uid, token: str) -> bool:
    """gate 用：uid + token 是否匹配。空 uid/token 一律拒绝（严格模式）。"""
    try:
        uid = int(uid)
    except Exception:
        return False
    if not token:
        return False
    row = _row(uid)
    if row is None:
        return False
    if row[6]:
        return False
    return secrets.compare_digest(str(row[3] or ""), str(token))


def profile(uid) -> dict:
    row = _row(int(uid))
    if row is None:
        return {}
    return {
        "uid": row[0],
        "username": row[1],
        "nickname": row[4] or row[1],
        "birthday": row[5] or "",
        "banned": bool(row[6]),
    }


def bind(uid: int, username: str, password: str, nickname: str = None, birthday: str = "") -> dict:
    """把已有玩家（例如 10001）绑定到一个可登录的账号上。"""
    ensure_schema()
    if _by_name(username) is not None:
        return {"ok": False, "error": "账号已存在"}
    if _row(uid) is not None:
        return {"ok": False, "error": f"uid {uid} 已绑定账号"}
    db.get_player_id(uid)  # 不存在则建
    token = secrets.token_hex(16)
    now = int(time.time())
    db.db.execute(
        "INSERT INTO users (id, username, password, user_token, nickname, birthday, created_at, last_login, banned) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
        (uid, username, hash_password(password), token, nick, birthday or "", now, now),
    )
    _apply_profile(uid, nick, birthday or "")
    db.save()
    return {"ok": True, "uid": uid, "username": username, "userToken": token}


def list_accounts(limit: int = 50) -> list:
    ensure_schema()
    rows = db.db.execute(
        "SELECT id, username, nickname, birthday, last_login, banned FROM users ORDER BY id LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "uid": r[0],
            "username": r[1],
            "nickname": r[2],
            "birthday": r[3],
            "lastLogin": r[4],
            "banned": bool(r[5]),
        }
        for r in rows
    ]


def set_nickname(uid, token: str, nickname: str = None, birthday: str = None) -> dict:
    """登录后改昵称/生日（需要有效 token）。"""
    if not verify(uid, token):
        return {"ok": False, "error": "登录状态无效"}
    uid = int(uid)
    row = _row(uid)
    if row is None:
        return {"ok": False, "error": "账号不存在"}
    cur_nick = row[4] or row[1]

    if nickname is not None and nickname.strip() and nickname.strip() != cur_nick:
        ok_nick, val = _check_nickname(nickname)
        if not ok_nick:
            return {"ok": False, "error": val}
        cur_nick = val
        db.set_players_info(uid, "player_name", cur_nick)
        db.db.execute("UPDATE users SET nickname = ? WHERE id = ?", (cur_nick, uid))
    if birthday is not None and birthday.strip():
        db.set_players_info(uid, "birthday", birthday.strip())
        db.db.execute("UPDATE users SET birthday = ? WHERE id = ?", (birthday.strip(), uid))
    db.save()
    return {"ok": True, **profile(uid)}