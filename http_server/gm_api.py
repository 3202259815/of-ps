"""GM 指令的 HTTP 入口。

容器里没有交互式控制台（prompt_toolkit 用不了），所以把原来控制台那套
cmd_exec.cmd_exec(cmd) 暴露成 HTTP 接口，用 token 保护。

用法：
    curl -X POST http://127.0.0.1:25565/gm/cmd \
         -H "X-GM-Token: 你的token" \
         -d "cmd=give 1000001 713285"

安全：Config.GM_HTTP_TOKEN 为空时接口直接 403（默认关闭）。
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.parse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from config import Config

router = APIRouter()

# 指令执行是串行的：concurrent 调用会互相干扰（背包读写、发通知）
_GM_LOCK = threading.Lock()


class _Capture(logging.Handler):
    """把执行期间的日志抓下来返回给调用方。"""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(record.getMessage())
        except Exception:  # noqa: BLE001
            pass


def _token_of(request: Request) -> str:
    return (
        request.headers.get("x-gm-token")
        or request.query_params.get("token")
        or ""
    ).strip()


async def _cmd_of(request: Request) -> str:
    # 支持三种：form(cmd=...)、json({"cmd": "..."})、纯文本 body
    # 注意：这里手工解析 urlencoded，不用 request.form()，
    # 否则会依赖 python-multipart，容器里没装就 500。
    ctype = (request.headers.get("content-type") or "").lower()
    raw_body = (await request.body()).decode("utf-8", "ignore")
    if "application/json" in ctype:
        try:
            body = json.loads(raw_body or "{}")
            if isinstance(body, dict):
                return str(body.get("cmd") or "").strip()
        except Exception:  # noqa: BLE001
            return ""
        return ""
    if "x-www-form-urlencoded" in ctype:
        parsed = urllib.parse.parse_qs(raw_body, keep_blank_values=True)
        vals = parsed.get("cmd") or []
        # 表单提交时 + 会被解码成空格，指令本身就是空格分隔，正合适
        return (vals[0] if vals else "").strip()
    return raw_body.strip()


def _run(cmd: str) -> dict:
    import utils.cmd_exec as cmd_exec

    cap = _Capture()
    root = logging.getLogger()
    root.addHandler(cap)
    try:
        with _GM_LOCK:
            cmd_exec.cmd_exec(cmd)
        ok = True
        err = ""
    except Exception as exc:  # noqa: BLE001
        ok = False
        err = f"{type(exc).__name__}: {exc}"
    finally:
        root.removeHandler(cap)
    return {"ok": ok, "cmd": cmd, "error": err, "output": cap.lines[-80:]}


@router.get("/gm/ping", response_class=PlainTextResponse)
async def gm_ping(request: Request):
    if not getattr(Config, "GM_HTTP_TOKEN", ""):
        return PlainTextResponse("gm disabled", status_code=403)
    if _token_of(request) != Config.GM_HTTP_TOKEN:
        return PlainTextResponse("bad token", status_code=403)
    return "pong"


@router.post("/gm/cmd")
async def gm_cmd(request: Request):
    token = getattr(Config, "GM_HTTP_TOKEN", "") or ""
    if not token:
        return JSONResponse(
            {"ok": False, "error": "GM 接口未开启：请在 .env 里设置 GM_HTTP_TOKEN"},
            status_code=403,
        )
    if _token_of(request) != token:
        return JSONResponse({"ok": False, "error": "token 不对"}, status_code=403)

    cmd = await _cmd_of(request)
    if not cmd:
        return JSONResponse({"ok": False, "error": "缺少 cmd 参数"}, status_code=400)

    result = _run(cmd)
    return JSONResponse(result)
