# Thin source-evolution layer over the pinned, source-built Codex image.
# Build the base image from the official openai/codex checkout first (README.md).
ARG CODEX_BASE=codex-src:2126f936
FROM ${CODEX_BASE}

RUN apt-get update && apt-get install -y --no-install-recommends rsync \
 && rm -rf /var/lib/apt/lists/*
COPY .proteus-boot.sh /usr/local/bin/codex-proteus-boot
RUN chmod +x /usr/local/bin/codex-proteus-boot \
 && tar -cf /opt/codex-source.tar \
      --exclude=codex-rs/target --exclude=Dockerfile.source \
      --exclude=docker-build.log --exclude=docker-build.pid \
      --exclude=UPSTREAM_COMMIT -C /src . \
 && mkdir /tmp/pristine \
 && tar -xf /opt/codex-source.tar -C /tmp/pristine \
 && codex-proteus-boot --proteus-tree-hash /tmp/pristine > /opt/pristine-hash \
 && rm -rf /tmp/pristine \
 && mkdir -p /codex-state /state \
 && chmod a+rwx /codex-state /state \
 && chmod -R a+rX /src /usr/local/cargo /usr/local/rustup \
 && chmod a+r /opt/codex-source.tar /opt/pristine-hash
WORKDIR /workspace
ENTRYPOINT ["codex-proteus-boot"]
