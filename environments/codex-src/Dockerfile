# Source-evolving OpenAI Codex environment for Proteus.
# Pin by immutable SHA for reproducible experiments.
FROM rust:1.95-bookworm

ARG CODEX_REF=eb15245d820d094cc0e5a950bc052bd7c2c5f925
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      git ca-certificates pkg-config libssl-dev libcap-dev clang cmake python3 jq rsync zstd \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/openai/codex.git /opt/src \
    && cd /opt/src \
    && git checkout --detach "$CODEX_REF" \
    && git submodule update --init --recursive

# Warm the exact Cargo dependency/build cache once in the image.
RUN cd /opt/src/codex-rs \
    && cargo fetch --locked \
    && CARGO_TARGET_DIR=/opt/codex-target cargo build --locked -p codex-cli --release

# Seed tar is exactly the source used above; generated build state and .git are excluded.
# The hash uses the same recipe as boot.sh's SOURCE_HASH so a first boot's freshly
# extracted /workspace/src hashes identically and can reuse /opt/codex-target as-is.
RUN tar --exclude=.git --exclude=target -cf /opt/codex-source.tar -C /opt/src . \
    && cp -a /usr/local/cargo /opt/cargo-home-seed \
    && (cd /opt/src && { find . -type f ! -path '*/.git/*' ! -path '*/target/*' -print0 | \
         sort -z | xargs -0 sha256sum; } | sha256sum | awk '{print $1}') > /opt/codex-source.sha256 \
    && chmod -R a+rX /opt/cargo-home-seed /opt/codex-target /opt/src /opt/codex-source.tar \
                      /opt/codex-source.sha256

COPY boot.sh /usr/local/bin/proteus-codex
RUN chmod +x /usr/local/bin/proteus-codex
ENTRYPOINT ["/usr/local/bin/proteus-codex"]
