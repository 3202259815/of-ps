import json
from config import Config


class DispatchHandler:
    @staticmethod
    def hot_update():
        return json.loads(
            '{"status":true,"message":"success","hotOssUrl":"'
            + Config.RES_URL
            + '","currentVersion":"'
            + Config.RES_VERSION
            + '","server":"cn_prod_main","ssAppId":"c969ebf346794cc797ed6eb6c3eac089","ssServerUrl":"https://te-of.inutan.com","open_gm":true,"open_error_log":true,"open_netConnecting_log":true,"ipAddress":"127.0.0.1","payUrl":"http://api-callback-of.inutan.com:19701","isTestServer":true,"error_log_level":2,"server_id":"10001","open_cs":true}'
        )

    @staticmethod
    def region_info(host_header: str = None):
        # 监听地址（0.0.0.0 / 空）不能直接发给客户端：客户端拿到 0.0.0.0 会连自己。
        # 这种情况回填「客户端访问 dispatch 时用的那个主机名/IP」，本地就是 127.0.0.1，
        # 公网就是玩家填进来的公网 IP 或域名，天然自适应。
        ip = (Config.GAME_SERVER_GADGET_IP or "").strip()
        if ip in ("", "0.0.0.0", "::", "*"):
            ip = "127.0.0.1"
            if host_header:
                h = host_header.strip()
                if h.startswith("["):           # [::1]:25565
                    h = h[1:h.find("]")] if "]" in h else h
                elif ":" in h:                  # 1.2.3.4:25565
                    h = h.rsplit(":", 1)[0]
                if h:
                    ip = h
        return json.loads(
            '{"status":true,"message":"success","gate_tcp_ip":"'
            + ip
            + '","gate_tcp_port":'
            + str(Config.GAME_SERVER_GADGET_PORT)
            + ',"is_server_open":true,"text":"","client_log_tcp_ip":"'
            + ip
            + '","client_log_tcp_port":'
            + str(Config.GAME_SERVER_GADGET_PORT)
            + ',"currentVersion":"'
            + Config.RES_VERSION
            + '","photo_share_cdn_url":"https://cdn-photo-of.inutan.com/cn_prod_main"}'
        )
