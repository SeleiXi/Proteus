# Prepared environments

One directory per harness: a pinned `Dockerfile` plus an `environment.toml` manifest. The
manifest is the single spec for both paths — build locally from the Dockerfile during
development, or pull the prebuilt image when one is published (`docker_image` short-circuits
the build, a mechanism we borrow from [Harbor](https://github.com/laude-institute/harbor)'s
task config).

An environment answers one question: *what does this harness need to run an episode that
the host should not have to provide?* Runtimes (Node for dsh), system packages, the harness
itself at a pinned version. The evolving state never lives in the image — it is always a
mounted workspace, so the image stays reusable across runs and arms.

## Manifest

```toml
[environment]
name = "deepseek-harness"
image = "proteus-env-dsh:0.1.0-rc.7"   # local tag the Dockerfile builds
# docker_image = "..."                  # set to a registry ref to skip the build
network = "host"                        # none | host (episodes that need an LLM API)
memory = "2g"
cpus = 2.0
env_passthrough = ["DEEPSEEK_API_KEY"]  # host env the episode may read

[harness]
adapter = "dsh"                         # proteus adapter that drives this environment
workspace_mount = "/workspace"          # where the evolving harness is mounted
state_mount = "/state"                  # harness-internal state (sessions, caches)
```

`proteus.sandbox.SandboxConfig.from_manifest(path)` loads the `[environment]` table.

## Status

| environment | harness | adapter | status |
|---|---|---|---|
| `deepseek-harness/` | [DeepSeek Harness (dsh)](https://github.com/deepseek-ai/deepseek-harness) | `dsh` | built + live-verified (headless episodes) |
| `aki/` | Aki research harness | `aki` | image assembled from the research checkout (private); adapter live-verified |
| `openhands/` | [OpenHands](https://github.com/All-Hands-AI/OpenHands) | — | manifest only; adapter not written |
| `swe-agent/` | [SWE-agent](https://github.com/SWE-agent/SWE-agent) | — | manifest only; adapter not written |

Rules for adding one (borrowed where noted from Harbor's conventions):
- **Pin everything**: base image tag + harness version in the Dockerfile; never `latest`.
- **Name images `proteus-env-<name>:<harness-version>`** so cleanup can match the prefix.
- **State out of the image**: workspace and harness state are mounts, declared in the
  manifest, so a snapshot of the mounts is a complete record of the run.
- **Network is a declared property** of the environment, not a flag someone remembers:
  `none` unless the harness itself must reach an API.
- Disable any harness telemetry in the image (e.g. `DSH_TELEMETRY_MODE=DISABLED`).
