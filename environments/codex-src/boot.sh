#!/bin/sh
set -eu

tree_hash() {
    find "$1" -type f -o -type l | LC_ALL=C sort | while IFS= read -r p; do
        rel=${p#"$1"/}
        printf '%s\0' "$rel"
        if [ -L "$p" ]; then readlink "$p"; else sha256sum "$p" | cut -d' ' -f1; fi
    done | sha256sum | cut -d' ' -f1
}

if [ "${1:-}" = "--proteus-tree-hash" ]; then
    tree_hash "$2"
    exit 0
fi

BIN=/src/codex-rs/target/release/codex
if [ -d /workspace/src/codex-rs ]; then
    HASH=$(tree_hash /workspace/src)
    if [ "$HASH" != "$(cat /opt/pristine-hash)" ]; then
        BIN=/state/codex-$HASH
        if [ ! -x "$BIN" ]; then
            BUILD=/state/build-$HASH
            mkdir -p "$BUILD"
            rsync -a --delete --exclude codex-rs/target /workspace/src/ "$BUILD/"
            if ! (cd "$BUILD" && CARGO_HOME=/usr/local/cargo CARGO_NET_OFFLINE=true \
                    CARGO_TARGET_DIR=/state/cargo-target CARGO_BUILD_JOBS=1 \
                    CARGO_PROFILE_RELEASE_LTO=false \
                    cargo build --manifest-path codex-rs/Cargo.toml -p codex-cli \
                    --release --bin codex \
                    > /state/last-build.log 2>&1); then
                echo "self-edited Codex source does not build:" >&2
                tail -40 /state/last-build.log >&2
                exit 97
            fi
            cp /state/cargo-target/release/codex "$BIN"
            chmod +x "$BIN"
        fi
    fi
fi

mkdir -p /codex-state
if [ ! -f /codex-state/auth.json ] && [ -f /run/proteus-codex-auth.json ]; then
    cp /run/proteus-codex-auth.json /codex-state/auth.json
    chmod 600 /codex-state/auth.json
fi
export CODEX_HOME=/codex-state

TRACE=
if [ "${1:-}" = "--proteus-trace" ]; then
    TRACE=$2
    shift 2
fi

if [ -n "$TRACE" ]; then
    "$BIN" "$@" </dev/null > "$TRACE"
    exit $?
fi
exec "$BIN" "$@"
