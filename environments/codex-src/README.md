# Codex, from source

This environment evolves the real Apache-2.0 `openai/codex` Rust source. It uses a
pinned source-built base image, extracts the exact source into each Proteus run, executes
the frozen active snapshot, and compiles a writable candidate only at an episode boundary.

Build on `cc_4090_2` after the base image exists:

```bash
cp environments/codex-src/boot.sh ~/seleixi/codex-src/.proteus-boot.sh
docker build -f environments/codex-src/Dockerfile \
  --build-arg CODEX_BASE=codex-src:2126f936 \
  -t proteus-env-codex-src:2126f936 ~/seleixi/codex-src
```

The image stores Cargo build products under the run-local `/state`; it never replaces
the host npm Codex installation. Runtime model access uses a read-only bind of the user's
`~/.codex/auth.json`, copied into framework-private state rather than the evolving tree.
Set `PROTEUS_CODEX_PROXY=http://127.0.0.1:7890` when the host reaches ChatGPT through a
local proxy; the adapter forwards it without placing the value in the evolving snapshot.
