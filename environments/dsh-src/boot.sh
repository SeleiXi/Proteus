#!/bin/sh
# Boot dsh from the seed's own source. /workspace/src (when present) is the agent's copy
# of the deepseek-harness monorepo source; the baked tree is made to match it EXACTLY (deletions and
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
# the container may run as an arbitrary host uid: give npm a writable HOME
export HOME=/tmp
SRC=/opt/src
CLI=$SRC/apps/cli/lib/bin.js
DISTS=$(cd "$SRC" && find apps packages vendor -type d -name lib -not -path '*/node_modules/*' 2>/dev/null | tr '
' ' ')

src_hash() {
    (cd /workspace/src && {
        find . -type f ! -path './node_modules/*' -print0 | sort -z \
            | tee /tmp/.pathlist | xargs -0 cat
        cat /tmp/.pathlist
    } | sha256sum | cut -d' ' -f1)
}

if [ -d /workspace/src/apps ]; then
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
        # files-only archive: a directory ENTRY makes tar chmod/utime that directory
        # on extraction, which a non-root uid cannot do to the baked root-owned tree.
        # File entries create missing parents quietly and touch nothing that exists.
        (cd /workspace/src && find . -type f ! -path './node_modules/*' -print0 \
            | tar -cf - --null -T -) \
            | tar -xf - -m --no-same-permissions -C "$SRC"
        if [ -f "/state/dist-$HASH.tar" ]; then
            tar -xf "/state/dist-$HASH.tar" -m --no-same-permissions -C "$SRC"
        else
            # build outputs must be derived from THIS source and nothing else: a stale
            # artifact of a deleted entry point would boot even though the snapshot says
            # the source is gone. Clean outputs, then build from scratch.
            (cd "$SRC" && {
            find apps packages vendor -type d -name lib -not -path '*/node_modules/*' \
                -exec rm -rf {} + 2>/dev/null || true
            find . -name '*.tsbuildinfo' -not -path './node_modules/*' -delete 2>/dev/null || true
            })
            if ! (cd "$SRC" && npm run build:lib >/state/last-build.log 2>&1); then
                echo "self-edited source does not build; tail of the build log:" >&2
                tail -20 /state/last-build.log >&2
                exit 97
            fi
            mkdir -p /state
            (cd "$SRC" && find $DISTS -type f -print0 \
                | tar -cf "/state/dist-$HASH.tar" --null -T -)
        fi
    fi
fi
exec node "$CLI" "$@"
