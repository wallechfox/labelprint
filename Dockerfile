# ============================================================
# LabelPrint —— 全国内源构建（飞牛 NAS 友好）
# 基础镜像默认用华为云 SWR 的 DockerHub 同步镜像，避免直连 Docker Hub 龟速。
#
# 若默认地址失效，构建时换一个即可（无需改文件）：
#   docker compose build --build-arg BASE_IMAGE=docker.xuanyuan.me/library/python:3.12-slim
# 备选：
#   swr.cn-south-1.myhuaweicloud.com/ddn-k8s/docker.io/library/python:3.12-slim
#   docker.1ms.run/library/python:3.12-slim
#   hub.rat.dev/library/python:3.12-slim
# ============================================================
ARG BASE_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/python:3.12-slim
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    LANG=C.UTF-8 \
    LABEL_PRINT_PORT=7900

# apt 换清华源；安装失败也不阻断构建（slim 镜像通常已自带 bash/iconv）
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    (apt-get update && apt-get install -y --no-install-recommends libc-bin bash && rm -rf /var/lib/apt/lists/*) || true

WORKDIR /app

COPY requirements.txt .
# pip 走清华源
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

COPY app ./app
COPY static ./static
COPY scripts ./scripts

# 防止 Windows 编辑导致的 CRLF 破坏 shell 脚本
RUN sed -i 's/\r$//' /app/scripts/*.sh && chmod +x /app/scripts/*.sh && mkdir -p /var/log

EXPOSE 7900

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.getenv('LABEL_PRINT_PORT','7900')+'/health',timeout=3)"

# LABEL_PRINT_RELOAD=true 时开启热重载：改 app/static/scripts 下的文件自动生效，无需 build
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"${LABEL_PRINT_PORT:-7900}\" $([ \"${LABEL_PRINT_RELOAD:-false}\" = \"true\" ] && echo --reload)"]
