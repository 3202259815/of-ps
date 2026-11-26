from fastapi import FastAPI, Request, Response, HTTPException, status
from fastapi.responses import JSONResponse, PlainTextResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pathlib import Path
import logging
import time
import uvicorn
import os
from urllib.parse import urlencode

from http_server.handlers.dispatch_handler import DispatchHandler
from http_server.handlers.oauth_handler import OAuthHandler
import utils.db as db
from utils import account
from config import Config

logger = logging.getLogger(__name__)

APP_DIR = Path("resources").resolve()
WEBSTATIC_DIR = APP_DIR / "webstatic"
ASSETS_DIR = APP_DIR / "assets"

app = FastAPI(title="gadget")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 手机端 QuickSDK v1/v2（AES/DES 表单）——实现见 http_server/sdk_quick.py
from http_server.sdk_quick import router as quick_router  # noqa: E402

app.include_router(quick_router)


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    nickname: str = ""
    birthday: str = ""


class CheckRequest(BaseModel):
    uid: int
    token: str = ""


class AccountUpdateRequest(BaseModel):
    uid: int
    token: str
    nickname: str = None
    birthday: str = None


def millis() -> int:
    return round(time.time() * 1000)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = (time.time() - start) * 1000.0
    logger.debug(
        "<< %s %s - %s (%.2f ms)",
        request.method,
        request.url.path,
        response.status_code,
        duration,
    )
    return response



PAD_PREFIXES = ("/llllllllllll",)


@app.middleware("http")
async def strip_pad_prefix(request: Request, call_next):
    path = request.scope.get("path", "")
    for pad in PAD_PREFIXES:
        if path.startswith(pad):
            path = path[len(pad):] or "/"
            break
    idx = path.find("/dispatch/")
    if idx > 0:
        path = path[idx:]
    request.scope["path"] = path
    request.scope["raw_path"] = path.encode()
    return await call_next(request)

@app.get("/", response_class=PlainTextResponse)
async def hello_world():
    return "hello, World"


@app.post("/dispatch/client_hot_update")
async def route_client_hot_update(request: Request):
    return JSONResponse(DispatchHandler.hot_update())


@app.get("/Resources/{version:path}/{filename:path}")
async def get_image(version: str, filename: str, request: Request):
    target = os.path.abspath(os.path.join(ASSETS_DIR, version, filename))
    if (
        not target.startswith(os.path.abspath(ASSETS_DIR) + os.sep)
        and os.path.abspath(ASSETS_DIR) != target
    ):
        # 不是子路径，拒绝
        raise HTTPException(status_code=400, detail="Illegal path")
    # 检查文件是否存在
    if not os.path.exists(target) or not os.path.isfile(target):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=target, filename=os.path.basename(target))


@app.post("/dispatch/region_info")
async def route_region_info(request: Request):
    return JSONResponse(DispatchHandler.region_info(request.headers.get("host")))


@app.post("/dispatch/get_notice_url_list")
async def get_notice_url_list():
    return JSONResponse({"data": []})


@app.post("/dispatch/get_notice_list")
async def get_notice_list():
    t = millis()
    return JSONResponse({"data": [], "serverTime": t})


@app.get("/config")
async def get_config():
    t = millis()
    return JSONResponse(
        {
            "code": 0,
            "data": {
                "server_timestamp": t,
                "sync_batch_size": 100,
                "sync_interval": 90,
            },
            "msg": "",
        }
    )


@app.get("/onlineHeartbeat")
async def online_heartbeat():
    return JSONResponse(
        {"status": True, "data": {"needLogout": 0, "needLogoutTip": 0}, "message": ""}
    )


@app.post("/openCenter/setGameRoleInfo")
async def set_game_role_info():
    return JSONResponse({"code": 0})


@app.post("/sync")
async def sync_endpoint():
    return JSONResponse({"status": True, "data": None, "message": "role up success"})


@app.post("/dispatch/get_login_url_list")
async def dispatch_get_login_url_list():
    return JSONResponse({})


@app.get("/open/oauth")
async def open_oauth():
    return OAuthHandler.oauth_page(WEBSTATIC_DIR)


@app.get("/openapi/oauth")
async def open_oauth():
    return OAuthHandler.oauth_page(WEBSTATIC_DIR)


@app.post("/api/login")
async def api_login(login: LoginRequest):
    user = db.get_sdk_user_info(login.username, login.password)
    if not user:
        return JSONResponse(
            {"success": False, "msg": "Incorrect password."}, status_code=200
        )

    uid = str(user["id"])
    username = user["username"]
    user_token = user["user_token"]

    # The Steam patch rejects unnamed characters; use the account name at creation.
    player_id = db.get_player_id(user["id"])
    if not db.get_players_info(player_id, "player_name")[0]:
        if not username or len(username.encode("utf-8")) > 48:
            return JSONResponse(
                {"success": False, "msg": "Username must be between 1 and 48 bytes."}
            )
        if db.get_player_name_exists(username):
            return JSONResponse(
                {"success": False, "msg": "Username is already used as a player name."}
            )
        db.set_players_info(player_id, "player_name", username)
        if not Config.SKIP_QUESTS:
            db.set_kv(player_id, "profile", "tutorial_pending", 1)

    success_url = "/loginSuccess.html?" + urlencode(
        {
            "uid": uid,
            "username": username,
            "userToken": user_token,
            "authToken": user_token,
            "timeleft": -1,
        }
    )

    return JSONResponse({"success": True, "successUrl": success_url})


@app.get("/loginSuccess.html")
async def login_success():
    return Response(content="", status_code=200)


# --------------------------------------------------------------------------- #
# 本地账号接口（登录器用；不走 QuickSDK 的 OAuth）
# --------------------------------------------------------------------------- #
@app.post("/account/register")
async def account_register(req: RegisterRequest):
    try:
        r = account.register(req.username, req.password, req.nickname, req.birthday)
    except Exception as e:  # 数据库异常也要给登录器一个明确的 JSON
        logger.exception("account_register failed")
        return JSONResponse({"ok": False, "error": f"服务端错误: {e}"}, status_code=200)
    if not r.get("ok"):
        return JSONResponse(r, status_code=200)
    logger.info(f"[account] register uid={r['uid']} username={r['username']}")
    return JSONResponse(r)


@app.post("/account/login")
async def account_login(req: LoginRequest):
    try:
        r = account.login(req.username, req.password)
    except Exception as e:
        logger.exception("account_login failed")
        return JSONResponse({"ok": False, "error": f"服务端错误: {e}"}, status_code=200)
    if not r.get("ok"):
        return JSONResponse(r, status_code=200)
    logger.info(f"[account] login uid={r['uid']} username={r['username']}")
    return JSONResponse(r)


@app.post("/account/check")
async def account_check(req: CheckRequest):
    return JSONResponse({"ok": account.verify(req.uid, req.token)})


@app.post("/account/update")
async def account_update(req: AccountUpdateRequest):
    try:
        r = account.set_nickname(req.uid, req.token, req.nickname, req.birthday)
    except Exception as e:
        logger.exception("account_update failed")
        return JSONResponse({"ok": False, "error": f"服务端错误: {e}"}, status_code=200)
    return JSONResponse(r)


@app.get("/account/profile")
async def account_profile(uid: int):
    p = account.profile(uid)
    if not p:
        return JSONResponse({"ok": False, "error": "账号不存在"}, status_code=200)
    return JSONResponse({"ok": True, **p})


def start():
    logger.info(f"HTTP server started at http://{Config.HTTP_HOST}:{Config.HTTP_PORT}")
    uvicorn.run(
        "http_server.server:app",
        host=Config.HTTP_HOST,
        port=Config.HTTP_PORT,
        log_level="warning",
        # uvicorn 默认会重建 root logger(把 main.py 装的日志文件 handler 冲掉),
        # 这里禁用它的日志配置, 保证 logs/server.log 一直有输出。
        log_config=None,
    )
