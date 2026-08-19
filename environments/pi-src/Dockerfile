# pi, built from its own source, bootable from an agent-edited copy of that source.
# Build context = a pi-mono checkout at the pinned tag; this file and boot.sh are copied
# into the context by `proteus env build` (or by hand, see environments/pi-src/README.md).
FROM node:24-slim
COPY . /opt/src
WORKDIR /opt/src
# bake dependencies, pinned model data (the one network fetch), and the pristine build
# model data is hydrated in the build context beforehand (one network fetch, pinned by
# the context checkout) — hydrating inside the build hits overlayfs EXDEV on rename
RUN npm ci --no-audit --no-fund \
 && npm run build:offline \
 && rm -rf /opt/src/.git \
 && find /opt/src -type f ! -path '*/node_modules/*' ! -path '*/dist/*' -print0 \
    | sort -z | xargs -0 cat | sha256sum | cut -d' ' -f1 > /opt/pristine-hash \
 && tar -cf /opt/pi-source.tar --exclude=node_modules --exclude='packages/*/dist' \
        --exclude='packages/*/*/dist' -C /opt src
COPY .proteus-boot.sh /usr/local/bin/pi-boot
RUN chmod +x /usr/local/bin/pi-boot
WORKDIR /workspace
ENTRYPOINT ["pi-boot"]
