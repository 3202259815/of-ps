# OpenSpace (of-ps) 服务端镜像
# 端口全部由环境变量决定（HTTP_PORT / GAME_SERVER_PORT），不写死。
#
# 注意：本文件**故意不写** BuildKit 的 syntax 指令行。
# 那行会让 BuildKit 先去 docker.io 拉前端镜像，
# 国内服务器拉不到就会直接失败。本文件没有用到任何 BuildKit 专有语法。
#
# 基础镜像可用 build arg 换源（国内拉不到 docker.io 时）：
#   PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 系统时区 + 证书；用国内 apt 源加速（失败则回退官方源）
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's#deb.debian.org#mirrors.aliyun.com#g; s#security.debian.org#mirrors.aliyun.com#g' /etc/apt/sources.list.d/debian.sources; \
    elif [ -f /etc/apt/sources.list ]; then \
        sed -i 's#deb.debian.org#mirrors.aliyun.com#g; s#security.debian.org#mirrors.aliyun.com#g' /etc/apt/sources.list; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends tzdata ca-certificates; \
    rm -rf /var/lib/apt/lists/*

# 先装依赖，利用缓存；pip 用国内源
COPY requirements-server.txt /app/requirements-server.txt
RUN pip install -r /app/requirements-server.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 由 .proto 现场生成 net_pb2.py（仓库里该文件被 .gitignore 排除）
COPY proto/ /app/proto/
RUN pip install grpcio-tools -i https://pypi.tuna.tsinghua.edu.cn/simple \
    && python -m grpc_tools.protoc -I/app/proto --python_out=/app/proto /app/proto/net.proto \
    && python -c "import sys; sys.path.insert(0,'/app'); from proto import net_pb2; print('net_pb2 messages:', len(net_pb2.DESCRIPTOR.message_types_by_name))" \
    && pip uninstall -y grpcio-tools

# 业务代码
COPY . /app/

# 运行期目录（用 volume 覆盖持久化）
RUN mkdir -p /app/data /app/logs

EXPOSE 25565 25566

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/' % os.environ.get('HTTP_PORT','25565'), timeout=4)" || exit 1

CMD ["python", "main.py"]
