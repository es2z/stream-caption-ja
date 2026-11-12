# 音声認識(Haas-ja-v1.0)+ 制御用 Web サーバー
#
# Haas-ASR は依存バージョンを厳しく固定しているので、同リポジトリのロックファイルで
# そのまま入れ、そこへこのアプリの依存を足す。
FROM python:3.12-slim-bookworm

ARG HAAS_COMMIT=acc614f68769dcbfae9dba96b48439c4a66295af

# Triton(flash-linear-attention が使う)は実行時に C コンパイラでカーネルを作るため build-essential が必要
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential git libsndfile1 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /opt/haas
RUN git clone https://github.com/third-intelligence/Haas-ASR.git . \
 && git checkout ${HAAS_COMMIT} \
 && uv sync --frozen --no-dev --group demo

ENV VIRTUAL_ENV=/opt/haas/.venv \
    PATH=/opt/haas/.venv/bin:$PATH

WORKDIR /opt/app
COPY pyproject.toml ./
COPY src ./src
RUN uv pip install --python /opt/haas/.venv/bin/python --no-deps . \
 && uv pip install --python /opt/haas/.venv/bin/python "websockets>=12" "httpx>=0.27" "pyyaml>=6"
# NeMo が実行時に .pyc を作ると WebSocket が壊れる不具合があるため(Haas-ASR の注記)、
# 事前に全部コンパイルしておく。一部のファイルが構文エラーでも続行する。
RUN python -m compileall -q /opt/haas/.venv/lib /opt/app/src; exit 0

ENV HF_HOME=/cache/huggingface \
    CONFIG_PATH=/config/config.yaml
EXPOSE 7860
CMD ["python", "-m", "stream_caption.main"]
