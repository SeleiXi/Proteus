#!/bin/sh
# Boot pi from the seed's own source. /workspace/src (when present) is the agent's copy
# of the pi-mono source; the baked tree is made to match it EXACTLY (deletions and
# renames materialize — the baked manifest lists every tracked file, and any not present
# in the workspace is removed before the overlay), rebuilt with the project's own
# toolchain when the source hash changes, and the built CLI is exec'd.
#
# The hash covers paths AND contents (the NUL-separated path list is appended to the
# concatenated bytes): a rename, an empty file, or a deletion always changes it. An
# untouched copy takes the pristine fast path with no copying at all. Build outputs are
# cached on /state keyed by the hash. The overlay excludes an agent-installed
# node_modules so it cannot shadow the baked dependencies.
set -e
SRC=/opt/src
CLI=$SRC/packages/coding-agent/dist/cli.js
DISTS="packages/tui/dist packages/telemetry/dist packages/ai/dist packages/agent/dist \
packages/session-backends/sqlite-node/dist packages/protocol/dist packages/client/dist \
packages/server/dist packages/coding-agent/dist"

src_hash() {
    (cd /workspace/src && {
        find . -type f ! -path './node_modules/*' -print0 | sort -z \
            | tee /tmp/.pathlist | xargs -0 cat
        cat /tmp/.pathlist
    } | sha256sum | cut -d' ' -f1)
}

if [ -d /workspace/src/packages ]; then
    HASH=$(src_hash)
    if [ "$HASH" = "$(cat /opt/pristine-hash)" ]; then
        :   # untouched source: the baked tree and build are exactly this source
    else
        # materialize deletions/renames of tracked files, then overlay the workspace
        if [ -f /opt/source-manifest.txt ]; then
            while IFS= read -r p; do
                [ -n "$p" ] || continue
                [ -e "/workspace/src/$p" ] || rm -f "$SRC/$p"
            done < /opt/source-manifest.txt
        fi
        (cd /workspace/src && tar -cf - --exclude='./node_modules' .) | tar -xf - -C "$SRC"
        if [ -f "/state/dist-$HASH.tar" ]; then
            tar -xf "/state/dist-$HASH.tar" -C "$SRC"
        else
            # build outputs must be derived from THIS source and nothing else: a stale
            # artifact of a deleted entry point would boot even though the snapshot says
            # the source is gone. Clean outputs, then build from scratch.
            (cd "$SRC" && {
            rm -rf $DISTS
            find . -name '*.tsbuildinfo' -not -path './node_modules/*' -delete 2>/dev/null || true
            })
            if ! (cd "$SRC" && npm run build:offline >/state/last-build.log 2>&1); then
                echo "self-edited source does not build; tail of the build log:" >&2
                tail -20 /state/last-build.log >&2
                exit 97
            fi
            mkdir -p /state
            (cd "$SRC" && tar -cf "/state/dist-$HASH.tar" $DISTS)
        fi
    fi
fi
exec node "$CLI" "$@"
