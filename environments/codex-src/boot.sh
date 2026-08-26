#!/bin/bash
set -euo pipefail

WORKSPACE=/workspace
SOURCE="$WORKSPACE/src"
STATE=/state
BUILD="$STATE/build-src"
TARGET="$STATE/target"
CARGO_HOME_STATE="$STATE/cargo-home"
HASH_FILE="$STATE/source.sha256"

mkdir -p "$STATE"
if [ ! -d "$CARGO_HOME_STATE/registry" ]; then
  rm -rf "$CARGO_HOME_STATE"
  cp -a /opt/cargo-home-seed "$CARGO_HOME_STATE"
fi
if [ ! -e "$TARGET/release/codex" ] && [ -e /opt/codex-target/release/codex ]; then
  rm -rf "$TARGET"
  cp -a /opt/codex-target "$TARGET"
fi
mkdir -p "$TARGET"
export CARGO_HOME="$CARGO_HOME_STATE"
export CARGO_TARGET_DIR="$TARGET"
export CODEX_HOME="$STATE/codex-home"
mkdir -p "$CODEX_HOME"

if [ ! -d "$SOURCE/codex-rs" ]; then
  echo "Proteus Codex source surface missing: $SOURCE/codex-rs" >&2
  exit 96
fi

# Relative paths so this matches the image's precomputed /opt/codex-source.sha256
# (hashed the same way from /opt/src at image-build time) byte-for-byte.
SOURCE_HASH="$(cd "$SOURCE" && { find . -type f \
    ! -path '*/.git/*' ! -path '*/target/*' -print0 | sort -z | \
    xargs -0 sha256sum 2>/dev/null || true; } | sha256sum | awk '{print $1}')"
OLD_HASH="$(cat "$HASH_FILE" 2>/dev/null || true)"
BIN="$TARGET/release/codex"
# codex-code-mode-host is a second binary this Codex build routes all tool/shell
# execution through; codex fails every tool call closed without it, so it is always
# built alongside codex-cli below, not treated as optional.

if [ -e /opt/codex-source.sha256 ] && [ ! -f "$HASH_FILE" ] \
   && [ "$SOURCE_HASH" = "$(cat /opt/codex-source.sha256)" ] && [ -x "$BIN" ]; then
  printf '%s\n' "$SOURCE_HASH" > "$HASH_FILE"
  OLD_HASH="$SOURCE_HASH"
fi

if [ "$SOURCE_HASH" != "$OLD_HASH" ] || [ ! -x "$BIN" ]; then
  rm -rf "$BUILD"
  mkdir -p "$BUILD"
  rsync -a --delete --exclude .git --exclude target "$SOURCE/" "$BUILD/"
  LOG="$STATE/build.log"
  # Baked into the image at build time: point Cargo at the pinned prebuilt V8 rather
  # than letting the `v8` crate's own build script try (and fail) to fetch one itself.
  if ! (cd "$BUILD/codex-rs" \
        && RUSTY_V8_ARCHIVE=/opt/rusty-v8-archive.a.gz \
           RUSTY_V8_SRC_BINDING_PATH=/opt/rusty-v8-binding.rs \
           cargo build --locked -p codex-cli -p codex-code-mode-host --release) >"$LOG" 2>&1; then
    echo "Codex candidate build failed" >&2
    tail -n 120 "$LOG" >&2 || true
    exit 97
  fi
  printf '%s\n' "$SOURCE_HASH" > "$HASH_FILE"
fi

# Proteus mode: preserve Codex' native JSONL in a state file while also keeping stdout.
if [ "${1:-}" = "--proteus-json-log" ]; then
  LOG_PATH="$2"
  shift 2
  mkdir -p "$(dirname "$LOG_PATH")"
  set +e
  "$BIN" "$@" | tee "$LOG_PATH"
  CODEX_RC=${PIPESTATUS[0]}
  set -e
  exit "$CODEX_RC"
fi

exec "$BIN" "$@"
