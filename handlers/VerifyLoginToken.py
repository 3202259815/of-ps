from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId
from proto.net_pb2 import (
    VerifyLoginTokenReq,
    VerifyLoginTokenRsp,
    StatusCode,
    PlayerOfflineRsp,
    PlayerOfflineReason,
)
import utils.db as db
from server.scene_data import _session_list as session_list, lock_session
from config import Config


@packet_handler(MsgId.VerifyLoginTokenReq)
class Handler(PacketHandler):
    def handle(self, session, data: bytes, packet_id: int):
        req = VerifyLoginTokenReq()
        req.ParseFromString(data)

        rsp = VerifyLoginTokenRsp()
        rsp.status = StatusCode.StatusCode_OK
        if not req.sdk_uid:
            req.sdk_uid = "9000000"
        _uid = db.get_user_id(req.sdk_uid)
        user_id = int(_uid)
        if Config.VERIFY_TOKEN and not db.verify_sdk_user_info(
            user_id, req.login_token
        ):
            rsp.status = StatusCode.StatusCode_FAIL
            session.send(MsgId.VerifyLoginTokenRsp, rsp, packet_id)
            return

        # 账号系统严格模式：uid + token 必须来自 /account/login
        if Config.ACCOUNT_STRICT_TOKEN:
            try:
                from utils import account as _account
                _ok = _account.verify(req.sdk_uid, req.login_token)
            except Exception:
                _ok = False
            if not _ok:
                rsp.status = StatusCode.StatusCode_INVALID_TOKEN
                session.send(MsgId.VerifyLoginTokenRsp, rsp, packet_id)
                return

        player_id = db.get_player_id(user_id)
        with lock_session:
            for i in session_list:
                if i.player_id == player_id:
                    rsp1 = PlayerOfflineRsp()
                    rsp1.status = StatusCode.StatusCode_OK
                    rsp1.reason = PlayerOfflineReason.PlayerOfflineReason_ANOTHER_LOGIN
                    if i.running == True and i.logged_in == True:
                        i.send(MsgId.PlayerOfflineRsp, rsp1, 0)
                        i.running = False
                        session_list.remove(i)
                        break

        session.verified = True
        session.player_id = player_id
        rsp.user_id = user_id
        rsp.account_type = req.account_type
        rsp.sdk_uid = req.sdk_uid
        rsp.is_server_open = True
        rsp.time_left = 4294967295
        rsp.device_uuid = req.device_uuid

        session.send(MsgId.VerifyLoginTokenRsp, rsp, packet_id)  # 1001,1002
