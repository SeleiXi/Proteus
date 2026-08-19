# Environment design (and what we borrowed from Harbor)

Proteus runs harnesses it does not control, so the environment a harness needs — runtime,
system packages, the harness itself — has to be packaged, pinned, and separated from the
evolving state. Before designing `environments/`, we surveyed
[Harbor](https://github.com/laude-institute/harbor) (Laude Institute; the framework behind
Terminal-Bench 2.0), which manages containerized environments for 40+ agents at scale.
This note records what we adopted, what we deliberately do differently, and why.

## Adopted from Harbor

1. **One manifest with a `docker_image` short-circuit.** Harbor's `task.toml` accepts
   either an `environment/Dockerfile` or a prebuilt `docker_image` ref; the same schema
   serves development builds and pinned releases. Our `environment.toml` does the same
   (`SandboxConfig.from_manifest`).
2. **Declared network policy.** Harbor tasks declare `network_mode` (`NO_NETWORK` |
   `PUBLIC` | `ALLOWLIST`) instead of leaving isolation to the invoker. Our manifests
   declare `network` per environment; `none` is the default in `SandboxConfig`.
3. **Date/version-tagged prebuilt images, never `latest`.** Harbor ships every
   Terminal-Bench task as a Docker Hub image tagged by build date. We tag
   `proteus-env-<name>:<harness-version>` (e.g. `proteus-env-dsh:0.1.0-rc.7`).
4. **Prefixed image naming for safe cleanup.** Harbor prefixes locally built images
   (`hb__*`) so `cache clean` can match them. Our `proteus-env-` prefix serves the same
   purpose.
5. **State outside the image.** Harbor injects agents into containers and collects
   artifacts from fixed mount points. We mount the evolving workspace and the harness's
   internal state (`/workspace`, `/state` for dsh); the image never carries run state, so
   one image serves every arm and seed.
6. **Resource limits in the manifest.** cpus / memory / storage live in Harbor's task
   config, not in runner flags. Ours declare `cpus` / `memory` per environment.

## Noted for later (not implemented)

- **Egress allowlist via a sidecar.** Harbor implements `ALLOWLIST` with a NET_ADMIN
  sidecar container and can switch policy mid-trial (agent offline, verifier online). The
  natural Proteus use: evolution episodes offline, evaluator phases online. Requires
  compose-level orchestration we don't have yet.
- **Reward-file contract.** Harbor verifiers write `/logs/verifier/reward.txt|json` —
  scoring decoupled from the harness language. A Proteus evaluator that reads a file the
  episode wrote would let any container self-report a score through the file boundary.
- **Digest pinning.** Harbor pins image *tags*; digests (`@sha256:`) are stricter and we
  should adopt them when we start publishing prebuilt images to a registry.
- **Oracle/no-op baselines.** Harbor validates environments by running the reference
  solution and a no-op agent through the identical pipeline. The Proteus analogue — a
  scripted adapter replaying a fixed action list through a real environment — would
  validate an environment before any model spends tokens in it.

## Deliberately different

- Harbor evaluates agents **on tasks**; the environment hosts one task attempt and is
  discarded. Proteus evolves harnesses **across episodes**; the environment is re-entered
  30+ times and the mounted workspace is the experiment's subject. Hence: no per-episode
  image rebuilds, snapshots of the mounts instead of artifact collection, and no
  benchmark registry — the `environments/` directory in-repo is the registry.
- Harbor's agent abstraction installs the agent *into* the task container at trial time.
  Proteus bakes the harness into the environment image at build time (pinned), because
  the harness is the constant apparatus and the workspace is the variable.

## Bringing your own environment

Nothing about the container is fixed. Point `--env` at an image reference, or at a
directory / `environment.toml` describing one, and the containerised harnesses evolve
inside it:

```bash
proteus run --harness dsh --env ghcr.io/you/your-env:1.4 --network host \
    --arm neutral --seeds 2 --episodes 10 --out runs/mine
```

A manifest is the same thing, versioned, with the settings attached:

```toml
[environment]
docker_image = "ghcr.io/you/your-env:1.4"   # or `image` for a tag you build locally
network      = "host"                        # none (default) | host | bridge | a named network
memory       = "8g"
cpus         = "4"
workdir      = "/workspace"
user         = "1000:1000"                   # avoid root-owned files in the mounts
env_passthrough = ["DEEPSEEK_API_KEY"]       # forwarded from your shell — secrets go here
docker_args  = ["--gpus", "all"]             # anything the fields above do not name

[[environment.mounts]]                       # extra bind mounts, repeatable
host      = "/data/corpora"
container = "/corpora"

[environment.env]                            # literal values, visible in the process table
LANG = "C.UTF-8"
```

```bash
proteus run --harness pi --env ./my-env --out runs/mine ...
```

Command-line flags (`--network`, `--mem`, `--cpus`, `--docker-arg`) override the manifest,
so one manifest can serve several runs. In Python the same object is passed directly, which
is also how a custom adapter accepts one:

```python
from proteus.sandbox import DockerSandbox, SandboxConfig
from proteus.adapters.dsh import DshHarness

env = SandboxConfig.from_spec("./my-env", network="host")
harness = DshHarness(sandbox=DockerSandbox(env), phase_timeout_s=1200)
```

Two requirements the image must meet: the harness binary is on the `PATH` (the adapter
appends its own arguments to whatever the image's entrypoint is), and the mount points the
adapter uses exist — `/workspace` for the evolving harness and `/state` for the harness's
own session storage, for both `dsh` and `pi`. Everything else is yours.

## Bounding an episode

Two independent limits, both per episode and both yours to set:

- `--max-turns` is the iteration budget: the number of steps an episode may take before it
  stops, enforced by the adapter rather than suggested to the model. The `minimal` and
  `llm` harnesses stop mid-phase when they reach it and record `turn_capped`.
- `--phase-timeout` is wall-clock seconds per phase for containerised harnesses, where the
  external CLI owns its own loop and a turn count is not ours to enforce. Reaching it ends
  the episode with a timeout error rather than hanging the sweep. Default 600.

Episode cost grows with episode index — later episodes wake up to a larger harness and read
more of it — so a cap that is comfortable at episode 1 is the one that matters at episode 30.
