# syntax=docker/dockerfile:1
# OpenSpace (of-ps) 服务端镜像
# 端口全部由环境变量决定（HTTP_PORT / GAME_SERVER_PORT），不写死。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 系统时区 + 依赖编译兜底
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖，利用缓存
COPY requirements-server.txt /app/requirements-server.txt
RUN pip install -r /app/requirements-server.txt

# 由 .proto 现场生成 net_pb2.py（仓库里该文件被 .gitignore 排除）
COPY proto/ /app/proto/
RUN pip install grpcio-tools \
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
