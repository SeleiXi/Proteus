# Codex source-evolving environment

This environment makes the **real `openai/codex` Rust source** a Proteus `loop` surface.
The Docker image pins an upstream commit, warms Cargo dependencies/build output, and stores
an exact source tar at `/opt/codex-source.tar`. Each Proteus run extracts that tar into
`harness/src/`.

During an episode, the frozen active harness is mounted read-only at `/workspace` and the
candidate is mounted at `/workspace/candidate`. `boot.sh` builds only the mounted source at
`/workspace/src`; the resulting binary and Cargo target cache live under `/state`, outside
the snapshotted harness. A failed candidate therefore cannot replace the active runtime.

The candidate-boundary gate runs in two stages over an offline Cargo cache: first
`cargo test --lib --no-run` for `codex-tui`/`codex-core`/`codex-cli` (a release build skips
`#[cfg(test)]` code, so this is what catches a candidate whose test modules no longer
compile), then the release build of `codex-cli` + `codex-code-mode-host`. Both profiles are
prewarmed into the image's `/opt/codex-target`; the boot wrapper copies that cache to
`/state` once per run root and recompiles only files whose contents actually changed
(content checksums are compared, snapshot mtimes are not trusted). The adapter allows up to
60 minutes for this boundary (`BOOT_TIMEOUT_S`); it only widens the wait, never the
build-success condition.

Build:

```bash
docker build -t proteus-env-codex-src:test-compile environments/codex-src
```

Authentication options:

1. `OPENAI_API_KEY` or `CODEX_API_KEY`; or
2. log in with Codex on the host (`~/.codex/auth.json`). `CodexHarness` copies only that
   auth file into the run-private `.codex-state/codex-home/`, outside the evolution history.

Smoke:

```bash
proteus check --harness codex
proteus check --harness codex --episode
```

Example sweep:

```bash
proteus run --harness codex \
  --arm neutral --arm review:skills --arm review:loop \
  --seeds 2 --episodes 3 --max-turns 80 \
  --out runs/codex-smoke

proteus measure --harness codex --travel --out runs/codex-smoke
```

Note: `codex exec` currently does not expose a native max-tool-call flag. The adapter stops
starting new phases after the Proteus episode budget is consumed, but one individual exec
can overshoot the remaining budget. The recorded trace/counters make that visible.
