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

Boot semantics (exact tree): tracked files deleted or renamed by the agent are removed from the baked tree before the overlay (the image carries the `git archive` manifest); the source hash covers paths as well as contents, so renames and empty files always re-key the build; the overlay excludes an agent-installed `node_modules`; and a rebuild first removes every build output and `.tsbuildinfo`, so artifacts are derived from the current source and a deleted entry point cannot boot from a stale bundle. An untouched copy boots via the pristine fast path with no copying at all.
