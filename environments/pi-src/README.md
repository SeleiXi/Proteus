# pi, from source

The image this directory builds is what makes pi's self-evolution operate on its **real
TypeScript source**: `/opt/src` is a pi-mono checkout with dependencies and a pristine
build baked in, and the entrypoint (`boot.sh`) syncs the agent's copy from
`/workspace/src` over that tree, rebuilds with the project's own toolchain when the
source hash changes (build outputs cached on `/state`), and execs the built CLI. An
untouched copy boots in seconds via the pristine-hash fast path; a broken edit exits 97
with the build log tail — that is the adapter's viability gate.

Rebuild (pin the tag you mean to study):

```bash
git clone --depth 1 --branch v0.84.2 https://github.com/badlogic/pi-mono /tmp/pi-src
# hydrate the model catalogs in the context — the one network fetch, pinned at bake time
docker run --rm -v /tmp/pi-src:/opt/src -w /opt/src --network host node:24-slim \
    sh -c 'npm ci --no-audit --no-fund && npm run hydrate:model-data'
rm -rf /tmp/pi-src/node_modules /tmp/pi-src/packages/*/dist /tmp/pi-src/packages/*/*/dist
cp environments/pi-src/boot.sh /tmp/pi-src/.proteus-boot.sh
docker build -f environments/pi-src/Dockerfile -t proteus-env-pi-src:0.84.2 /tmp/pi-src
```

Attribution: pi-mono (github.com/badlogic/pi-mono), MIT.

Known issue: the boot wrapper syncs `/workspace/src/.` over the baked tree without excluding a `node_modules/` the agent may have installed into its own source, which can shadow the baked dependencies. Until the wrapper excludes it (needs an image rebake), instruct agents not to install dependencies into `src/`.
