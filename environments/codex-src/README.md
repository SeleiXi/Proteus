# Codex, from source

This environment evolves the real Apache-2.0 `openai/codex` Rust source. It uses a
pinned source-built base image, extracts the exact source into each Proteus run, executes
the frozen active snapshot, and compiles a writable candidate only at an episode boundary.
The build uses the upstream-pinned Rust 1.95 toolchain. Release ThinLTO is disabled so
episode-boundary viability builds remain practical; this changes optimization only.

Build the pinned base from an exact official checkout, then add the Proteus runtime layer:

```bash
git clone --depth 1 --branch rust-v0.149.1 \
  https://github.com/openai/codex.git ~/seleixi/codex-src
docker build -f ~/seleixi/proteus/environments/codex-src/Dockerfile.base \
  -t codex-src:ff29a443 ~/seleixi/codex-src
cp environments/codex-src/boot.sh ~/seleixi/codex-src/.proteus-boot.sh
docker build -f environments/codex-src/Dockerfile \
  --build-arg CODEX_BASE=codex-src:ff29a443 \
  -t proteus-env-codex-src:ff29a443 ~/seleixi/codex-src
```

The tag resolves to official commit `ff29a44391deccde0aba0f8390337d7f3c319ea4` and
matches the stable Codex 0.149.1 protocol. To reuse an earlier source build's Cargo
artifacts, add `--build-arg CODEX_CACHE_BASE=codex-src:<old-sha>` to the base build.

The image stores Cargo build products under the run-local `/state`; it never replaces
the host npm Codex installation. Runtime model access uses a read-only bind of the user's
`~/.codex/auth.json`, copied into framework-private state rather than the evolving tree.
Set `PROTEUS_CODEX_PROXY=http://127.0.0.1:7890` when the host reaches ChatGPT through a
local proxy; the adapter forwards it without placing the value in the evolving snapshot.
