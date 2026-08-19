# dsh, built from its own source, bootable from an agent-edited copy of that source.
# Build context = a deepseek-harness checkout at the pinned tag WITH its .git (the
# source tar is produced by `git archive`, so it is exactly the tracked files).
FROM node:24-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*
COPY . /opt/src
WORKDIR /opt/src
# NOTE: bake with `docker build --network host` on macOS — the default bridge goes
# through vpnkit NAT, whose connections exhaust under pnpm's parallel registry fetches
# (the same reason runtime containers use host networking).
RUN corepack enable && pnpm install --frozen-lockfile
RUN npm run build:lib
RUN git archive --format=tar -o /opt/dsh-source.tar HEAD \
 && rm -rf /opt/src/.git \
 && mkdir /tmp/pristine && tar -xf /opt/dsh-source.tar -C /tmp/pristine \
 && (cd /tmp/pristine && find . -type f ! -path './node_modules/*' -print0 \
     | sort -z | xargs -0 cat | sha256sum | cut -d' ' -f1) > /opt/pristine-hash \
 && rm -rf /tmp/pristine
COPY .proteus-boot.sh /usr/local/bin/dsh-boot
RUN chmod +x /usr/local/bin/dsh-boot
ENV DSH_HOME=/state
ENV DSH_TELEMETRY_MODE=DISABLED
WORKDIR /workspace
ENTRYPOINT ["dsh-boot"]
