#!/bin/sh
# Boot dsh from the seed's own source (see environments/pi-src/boot.sh — same design).
# /workspace/src is the agent's copy of the deepseek-harness monorepo source; it is
# synced over the baked tree, rebuilt with the project's own toolchain (tsc -b is
# incremental against the baked .tsbuildinfo) when the source hash changes, and the
# built CLI is exec'd. Build outputs are cached on /state keyed by source hash.
set -e
SRC=/opt/src
CLI=$SRC/apps/cli/lib/bin.js

if [ -d /workspace/src/apps ]; then
    cp -R /workspace/src/. "$SRC"/
    HASH=$(cd /workspace/src && find . -type f ! -path './node_modules/*' -print0 \
           | sort -z | xargs -0 cat | sha256sum | cut -d' ' -f1)
    if [ "$HASH" = "$(cat /opt/pristine-hash)" ]; then
        :   # untouched source: the baked build is exactly this source
    elif [ -f "/state/dist-$HASH.tar" ]; then
        tar -xf "/state/dist-$HASH.tar" -C "$SRC"
    else
        if ! (cd "$SRC" && npm run build:lib >/state/last-build.log 2>&1); then
            echo "self-edited source does not build; tail of the build log:" >&2
            tail -20 /state/last-build.log >&2
            exit 97
        fi
        mkdir -p /state
        (cd "$SRC" && find apps packages vendor -type d -name lib \
            -not -path '*/node_modules/*' 2>/dev/null | tar -cf "/state/dist-$HASH.tar" -T -)
    fi
fi
exec node "$CLI" "$@"
