#!/bin/sh
# Boot pi from the seed's own source. /workspace/src (when present) is the agent's copy
# of the pi-mono source; it is synced over the baked tree, rebuilt with the project's own
# toolchain when its content hash changes, and the built CLI is exec'd. Build outputs are
# cached on /state so unchanged phases boot in seconds. No src mounted -> the baked build.
set -e
SRC=/opt/src
CLI=$SRC/packages/coding-agent/dist/cli.js
DISTS="packages/tui/dist packages/telemetry/dist packages/ai/dist packages/agent/dist \
packages/session-backends/sqlite-node/dist packages/protocol/dist packages/client/dist \
packages/server/dist packages/coding-agent/dist"

if [ -d /workspace/src/packages ]; then
    cp -R /workspace/src/. "$SRC"/
    HASH=$(cd /workspace/src && find . -type f ! -path './node_modules/*' -print0 \
           | sort -z | xargs -0 cat | sha256sum | cut -d' ' -f1)
    if [ "$HASH" = "$(cat /opt/pristine-hash)" ]; then
        :   # untouched source: the baked build is exactly this source
    elif [ -f "/state/dist-$HASH.tar" ]; then
        tar -xf "/state/dist-$HASH.tar" -C "$SRC"
    else
        if ! (cd "$SRC" && npm run build:offline >/state/last-build.log 2>&1); then
            echo "self-edited source does not build; tail of the build log:" >&2
            tail -20 /state/last-build.log >&2
            exit 97
        fi
        mkdir -p /state
        (cd "$SRC" && tar -cf "/state/dist-$HASH.tar" $DISTS)
    fi
fi
exec node "$CLI" "$@"
