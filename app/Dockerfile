# syntax=docker/dockerfile:1
# 音声認識(Haas-ja-v1.0)+ 制御用 Web サーバー
#
# Haas-ASR は依存バージョンを厳しく固定しているので、同リポジトリのロックファイルで
# そのまま入れ、そこへこのアプリの依存を足す。
#
# uv のダウンロードキャッシュはビルド用のキャッシュ(--mount=type=cache)に置き、イメージには含めない。
# PyTorch / CUDA ライブラリがキャッシュと仮想環境に二重に入るのを防ぐため。
# 再ビルド時はこのキャッシュが再利用されるので、ダウンロードし直しにはならない。
FROM python:3.12-slim-bookworm

ARG HAAS_COMMIT=acc614f68769dcbfae9dba96b48439c4a66295af

# Triton(flash-linear-attention が使う)は実行時に C コンパイラでカーネルを作るため build-essential が必要
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential git libsndfile1 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
# キャッシュ用マウントは別のファイルシステムなのでハードリンクできない。copy を明示して警告を出さないようにする
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_CACHE_DIR=/root/.cache/uv \
    PYTHONUNBUFFERED=1

WORKDIR /opt/haas
# 指定コミットだけを浅く取得し、履歴(.git)はイメージに残さない
RUN git init -q . \
 && git remote add origin https://github.com/third-intelligence/Haas-ASR.git \
 && git fetch -q --depth 1 origin ${HAAS_COMMIT} \
 && git checkout -q FETCH_HEAD \
 && rm -rf .git
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --group demo

ENV VIRTUAL_ENV=/opt/haas/.venv \
    PATH=/opt/haas/.venv/bin:$PATH

WORKDIR /opt/app
COPY pyproject.toml ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/haas/.venv/bin/python --no-deps . \
 && uv pip install --python /opt/haas/.venv/bin/python "websockets>=12" "httpx>=0.27" "pyyaml>=6"
# NeMo が実行時に .pyc を作ると WebSocket が壊れる不具合があるため(Haas-ASR の注記)、
# 事前に全部コンパイルしておく。一部のファイルが構文エラーでも続行する。
RUN python -m compileall -q /opt/haas/.venv/lib /opt/app/src; exit 0

ENV HF_HOME=/cache/huggingface \
    CONFIG_PATH=/config/config.yaml
EXPOSE 7877
CMD ["python", "-m", "stream_caption.main"]
