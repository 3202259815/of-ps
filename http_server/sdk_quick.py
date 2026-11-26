"""QuickSDK v1/v2 接口（手机端）。

算法与路由参考 Lolo（D:\\OpenSpace_Extract\\lolo\\pkg\\alg\\endecode.go、sdk\\router.go）：
  - V1/V2 请求体是表单：data=<base64(AES-128-ECB(明文))>，sign=md5(排序后的 k=v& + singKey)
  - V2 无 data 时用 json_data=<base64(DES-ECB(明文))>
  - 响应是普通 JSON：{"result":true,"status":true,"data":{...},"message":"","error":{"id":0,"message":""}}

账号复用 utils.account（users 表），uid 就是 player_id，token 就是 users.user_token，
gate 侧 handlers/VerifyLoginToken.py 在 ACCOUNT_STRICT_TOKEN=True 时用 account.verify 校验。
"""
import base64
import hashlib
import json
import logging
import time
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

import utils.db as db
from utils import account

logger = logging.getLogger("sdk_quick")

SING_KEY = b"0b2a18e45d7df321"
DES_KEY = b"78143304"

router = APIRouter()

# ---------------------------------------------------------------- 加解密

def _unpad(b: bytes) -> bytes:
    if not b:
        return b
    n = b[-1]
    if 1 <= n <= 16 and b[-n:] == bytes([n]) * n:
        return b[:-n]
    return b


def aes_ecb_decrypt(b64: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    raw = b64.strip()
    try:
        ct = base64.b64decode(raw + "=" * (-len(raw) % 4))
    except Exception:
        ct = base64.b64decode(raw)
    dec = Cipher(algorithms.AES(SING_KEY), modes.ECB()).decryptor()
    return _unpad(dec.update(ct) + dec.finalize())


def des_ecb_decrypt(b64: str) -> bytes:
    raw = b64.strip()
    try:
        ct = base64.b64decode(raw + "=" * (-len(raw) % 4))
    except Exception:
        ct = base64.b64decode(raw)
    try:
        from Crypto.Cipher import DES as _DES  # pycryptodome

        return _unpad(_DES.new(DES_KEY, _DES.MODE_ECB).decrypt(ct))
    except Exception as e:  # pragma: no cover
        raise RuntimeError("DES 解密不可用：%s" % e)


def sign_of(plain: bytes) -> str:
    """md5(sorted k=v& ... + singKey)，与 Lolo 的 SingBytes 一致。"""
    try:
        obj = json.loads(plain.decode("utf-8"))
    except Exception:
        return ""
    items = sorted((str(k), obj[k]) for k in obj)
    parts = []
    for k, v in items:
        if isinstance(v, bool):
            v = "true" if v else "false"
        parts.append("%s=%s" % (k, v))
    s = "&".join(parts) + SING_KEY.decode()
    return hashlib.md5(s.encode("utf-8")).hexdigest()


async def read_plain(request: Request, check_sign: bool = False) -> dict:
    """解出请求明文 JSON。V1 由调用方传 check_sign=False。"""
    ctype = (request.headers.get("content-type") or "").lower()
    form = {}
    raw_body = await request.body()
    if "json" in ctype:
        try:
            return json.loads(raw_body or b"{}")
        except Exception:
            return {}
    if "x-www-form-urlencoded" in ctype or not ctype:
        form = dict(parse_qsl(raw_body.decode("utf-8", "ignore"), keep_blank_values=True))
    else:
        # multipart 或其它：尝试从 query 取
        form = dict(request.query_params)

    data = form.get("data") or request.query_params.get("data")
    if data:
        plain = aes_ecb_decrypt(data)
        if check_sign:
            want = form.get("sign") or request.query_params.get("sign") or ""
            got = sign_of(plain)
            if want and got and want.lower() != got.lower():
                logger.warning("sign 不匹配 want=%s got=%s", want, got)
        try:
            return json.loads(plain.decode("utf-8"))
        except Exception:
            logger.warning("data 解密后不是 JSON: %r", plain[:120])
            return {}
    jd = form.get("json_data") or request.query_params.get("json_data")
    if jd:
        plain = des_ecb_decrypt(jd)
        try:
            return json.loads(plain.decode("utf-8"))
        except Exception:
            return {}
    # 没加密：直接当普通表单/JSON
    return form


# ---------------------------------------------------------------- 响应包装

def ok(data=None):
    return JSONResponse(
        {
            "result": True,
            "status": True,
            "data": data,
            "message": "",
            "error": {"id": 0, "message": ""},
        }
    )


def fail(msg: str, code: int = 1):
    return JSONResponse(
        {
            "result": False,
            "status": False,
            "data": None,
            "message": msg,
            "error": {"id": code, "message": msg},
        }
    )


# ---------------------------------------------------------------- 账号辅助

def _row_by_token(token: str):
    if not token:
        return None
    rows = db.db.execute(
        "SELECT id, username, nickname FROM users WHERE user_token = ?", (str(token),)
    ).fetchall()
    return rows[0] if rows else None


def _refresh_token(uid: int) -> str:
    import secrets

    token = secrets.token_hex(16)
    db.db.execute("UPDATE users SET user_token = ?, last_login = ? WHERE id = ?",
                  (token, int(time.time()), uid))
    db.save()
    return token


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


# ---------------------------------------------------------------- 系统初始化

V1_PRODUCT_CONFIG = {
    "useSms": "1",
    "fcmTips": None,
    "joinQQGroup": {"groupNum": "", "groupKey": ""},
    "serviceInfo": "",
    "isFloat": "0",
    "mainLogin": "0",
    "useService": "0",
    "useServiceCenter": "0",
    "logo": "",
    "useBBS": "0",
    "gift": "0",
    "isShowFloat": "0",
    "autoOpenAgreement": "1",
    "mainLoginType": "3",
    "ucentUrl": "http://sdkapi-of.inutan.com/userCenter/play",
    "useCpLogin": "0",
    "floatLogo": "",
    "theme": "FF7E0C",
    "useAppAuth": "0",
    "switchWxAppPlug": "0",
    "idverifyTipTit": "",
    "banshuSwitch": "0",
    "rmAccountLg": "0",
    "regVerifyCode": "1",
    "disFastReg": "1",
    "noPassWallet": "0",
    "hideMyFunc": {"hideRegBtn": 0, "custAdReport": 0,
                   "normalUserBindPhone": 0, "enableEvt": 0},
    "title": "",
    "skinStyle": "",
    "rmGuestLg": 0,
}

PAY_TYPES = [
    {"payTypeId": "226", "payTypeName": "微信支付"},
    {"payTypeId": "1", "payTypeName": "支付宝"},
]


@router.post("/v1/system/init")
async def v1_system_init(request: Request):
    req = await read_plain(request, check_sign=False)
    logger.info("[quick] v1 system/init productCode=%s os=%s", req.get("productCode"), req.get("os"))
    return ok({
        "payTypes": PAY_TYPES,
        "version": {"versionName": "", "versionNo": 1, "versionUrl": "",
                    "updateTime": "", "isMust": "0", "updateTips": ""},
        "realnameNode": "2",
        "productConfig": V1_PRODUCT_CONFIG,
        "clientIp": _client_ip(request),
    })


@router.post("/v2/system/init")
async def v2_system_init(request: Request):
    req = await read_plain(request, check_sign=True)
    logger.info("[quick] v2 system/init productCode=%s", req.get("productCode"))
    return ok({
        "ptConfig": V1_PRODUCT_CONFIG,
        "ptVer": {"versionName": "", "versionNo": 1, "versionUrl": "",
                  "updateTime": "", "isMust": "0", "updateTips": ""},
        "realnameNode": "2",
        "clientIp": _client_ip(request),
    })


# ---------------------------------------------------------------- 登录

def _login_result_v1(uid: int, username: str, nickname: str, token: str):
    return {
        "extInfo": {"oauthType": 0, "oauthId": "", "access_token": ""},
        "isAdult": True,
        "uAge": 9999,
        "guestRealName": 1,
        "authToken": token,
        "userData": {
            "uid": str(uid),
            "username": username,
            "token": token,
            "isGuest": 0,
            "isMbUser": 1,
            "isSnsUser": 0,
            "mobile": "188****8888",
        },
    }


def _login_result_v2(uid: int, username: str, token: str):
    return {
        "extInfo": {"oauthType": 0, "oauthId": "", "access_token": ""},
        "isAdult": True,
        "uAge": 9999,
        "guestRealName": 1,
        "authToken": token,
        "userData": {
            "uid": str(uid),
            "username": username,
            "mobile": "188****8888",
            "isGuest": "0",
            "regDevice": "android",
            "sexType": "0",
            "isMbUser": 1,
            "isSnsUser": 0,
            "token": token,
        },
    }


def _do_login(username: str, password: str):
    res = account.login(username, password)
    if not res.get("ok"):
        return None, res.get("error") or "登录失败"
    return res, None


async def _handle_login(request: Request, v2: bool):
    req = await read_plain(request, check_sign=v2)
    username = str(req.get("username") or req.get("account") or "").strip()
    password = str(req.get("password") or req.get("pwd") or "")
    logger.info("[quick] loginByName v%d username=%s", 2 if v2 else 1, username)
    if not username or not password:
        return fail("账号或密码为空")
    res, err = _do_login(username, password)
    if res is None:
        return fail(err)
    uid = int(res["uid"])
    token = res["userToken"]
    if v2:
        return ok(_login_result_v2(uid, res["username"], token))
    return ok(_login_result_v1(uid, res["username"], res.get("nickname") or res["username"], token))


@router.post("/v1/user/loginByName")
async def v1_login_by_name(request: Request):
    return await _handle_login(request, v2=False)


@router.post("/v2/user/loginByName")
async def v2_login_by_name(request: Request):
    return await _handle_login(request, v2=True)


async def _handle_auto_login(request: Request, v2: bool):
    req = await read_plain(request, check_sign=v2)
    auth = str(req.get("authToken") or req.get("token") or "")
    logger.info("[quick] autoLogin v%d", 2 if v2 else 1)
    row = _row_by_token(auth)
    if row is None:
        return fail("登录状态已失效")
    uid = int(row[0])
    username = row[1] or ""
    nickname = row[2] or username
    token = _refresh_token(uid)
    if v2:
        return ok(_login_result_v2(uid, username, token))
    return ok(_login_result_v1(uid, username, nickname, token))


@router.post("/v1/user/autoLogin")
async def v1_auto_login(request: Request):
    return await _handle_auto_login(request, v2=False)


@router.post("/v2/user/autoLogin")
async def v2_auto_login(request: Request):
    return await _handle_auto_login(request, v2=True)


@router.post("/v1/auth/getUserInfo")
async def v1_get_user_info(request: Request):
    req = await read_plain(request, check_sign=False)
    uid = req.get("uid") or req.get("userId")
    row = _row_by_token(req.get("token") or req.get("authToken") or "")
    if row is not None:
        uid = row[0]
    if uid is None:
        return fail("缺少用户标识")
    info = account.profile(uid)
    if not info:
        return fail("用户不存在")
    return ok({
        "isBindPhone": 1,
        "nickName": info.get("nickname") or info.get("username"),
        "phone": "188****8888",
        "sexType": 0,
        "regType": "3",
        "lastLoginTime": int(time.time()),
        "fcmShowTips": 0,
        "isAdult": 1,
        "timeleft": 0,
        "bindInfo": {"isBindPhone": 1, "isBindWx": 0, "isBindQq": 0},
    })


@router.post("/v1/auth/asyUonline")
async def v1_asy_online(request: Request):
    await read_plain(request, check_sign=False)
    return ok([])


@router.post("/v2/users/checkLogin")
async def v2_check_login(request: Request):
    req = await read_plain(request, check_sign=True)
    row = _row_by_token(req.get("token") or req.get("authToken") or "")
    if row is None:
        return fail("登录状态已失效")
    return ok({"uid": str(row[0]), "username": row[1] or ""})


# ---------------------------------------------------------------- dispatch 补充

@router.get("/dispatch/get_client_black_list")
async def get_client_black_list():
    return ok({"blackList": [], "black_list": [], "list": []})


@router.head("/dispatch/region_info")
async def head_region_info():
    return PlainTextResponse("Lolo!")


@router.post("/v3/bind")
async def v3_bind():
    return ok({})


@router.get("/resolve")
async def resolve(domain: str = ""):
    return JSONResponse({
        "host": domain,
        "ttl": 60,
        "ips": ["127.0.0.1"],
        "cip": "127.0.0.1",
        "cl": [3],
    })
