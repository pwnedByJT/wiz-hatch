# wiz-hatch

`wiz-hatch` is a small, security-focused Discord bot that reproduces the
[Wizard101 Pet Return Chance Calculator](https://petbodyw101.vercel.app/) as a
native `/hatch` slash command. It also provides `/pet`, `/stats`, and `/wiki`
utilities backed by Wizard101 Central Wiki links. All four commands use a
checked-in catalog of 1,410 pet bodies and perform command handling in memory.

The project targets Python 3.11 or newer, `discord.py` 2.x, ARM64 containers,
and a Raspberry Pi k3s cluster. It is not affiliated with KingsIsle
Entertainment or Wizard101.

## How the calculation works

For non-exclusive right-slot bodies, the calculator uses the same formulas as
the reference application:

```text
left chance  = (11 - left wow factor)  / (22 - left wow factor - right wow factor)
right chance = (11 - right wow factor) / (22 - left wow factor - right wow factor)
```

Each result is rounded with JavaScript `Math.round` semantics to match the web
calculator. A body with a higher wow factor has a lower return chance. When an
exclusive pet is placed in the right slot of a self-hatch, it cannot be
returned, so the left body has a 100% return chance. An exclusive body in the
left slot still uses the normal wow-factor formula.

## Slash commands

- `/hatch left_pet right_pet` calculates both body return chances, links each
  pet to its Wizard101 Central Wiki page, and shows cumulative odds for 3, 5,
  and 10 hatches plus 50% and 90% confidence counts.
- `/pet pet_name` inspects Wow Factor and Exclusive status, links to the Pet
  Locator and Pet Stat Calculator, and compares the pet with a WF 10
  Kiosk/Sticky Base.
- `/stats strength intellect agility will power mighty_or_thinkin_cap` computes
  exact and half-to-even rounded Pet 2.0 talent values. Defaults are
  `255/250/260/260/250`; stats are bounded from 0 through 350, and the optional
  cap flag applies Mighty's +65 Strength bonus.
- `/wiki query category` opens a safely encoded Wiki search or category link.
  Exact catalog pet names receive a direct pet-page link, along with Pet,
  Talent, Jewel, and Snack locator links.

Wiki URLs are generated locally. No slash command performs outbound network or
filesystem I/O while handling an interaction.

## Discord setup

1. Create an application in the
   [Discord Developer Portal](https://discord.com/developers/applications), add
   a bot, and reset/copy its token.
2. On **OAuth2 > URL Generator**, select the `bot` and `applications.commands`
   scopes. The bot does not require any guild permissions.
3. Install the generated URL into the target server.
4. Store the token only in the `DISCORD_TOKEN` environment variable or the
   GitHub Actions `DISCORD_TOKEN` secret. Never commit it.

The bot requests no privileged gateway intents and disables allowed mentions.

## Development workflow

All work uses GitHub Flow. Never modify files or commit directly on `main`.
Before changing any file, update local `main` and create a descriptive branch:

```bash
git fetch origin
git checkout main
git pull --ff-only origin main
git checkout -b <type>/<short-description>
```

Use `feat/`, `fix/`, `chore/`, `sec/`, or `docs/` as the branch prefix. Commit
and push changes only to that branch, then open a GitHub Pull Request targeting
`main`. Merge through the Pull Request only after CI passes `ruff`, `bandit`,
and `pytest`; never bypass the Pull Request workflow.

Advance releases with Semantic Versioning; the current feature release is
`0.1.0`. Create release tags from `main` only after merge.

## Local development

Create an isolated environment and install the exact development dependencies:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --requirement requirements-dev.txt
$env:DISCORD_TOKEN = "your token held only in this shell"
$env:DISCORD_GUILD_ID = "your development server ID"
python -m wiz_hatch.bot
```

For Bash, activate with `source .venv/bin/activate` and set variables with
`export`. `DISCORD_GUILD_ID` is optional. When set, commands synchronize only
to that development guild and usually appear immediately. When omitted, the
bot registers global commands, whose propagation is controlled by Discord.

Configuration:

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DISCORD_TOKEN` | Yes | None | Discord bot token |
| `DISCORD_GUILD_ID` | No | Global sync | Development guild snowflake |
| `LOG_LEVEL` | No | `INFO` | Python log level |
| `HEALTH_HOST` | No | `0.0.0.0` | Probe listener address |
| `HEALTH_PORT` | No | `8080` | Probe listener port |
| `WIZ_HATCH_DATA_PATH` | No | `data/pets.json` | Absolute or relative catalog path |

Run all local checks with:

```bash
make check
```

The equivalent commands are `python -m ruff format --check .`,
`python -m ruff check .`, `python -m bandit --recursive src --skip B104,B110`,
`python -m mypy`, and `python -m pytest`.

## Pet data and autocomplete

The catalog is generated from the public reference application's client bundle:

```bash
python scripts/build_w101_pets.py
```

Review catalog changes before committing them. This script is the only feature
that fetches the reference site. The running bot reads `data/pets.json` exactly
once while loading its command cog. Every autocomplete interaction then uses a
normalized, immutable in-memory index, returns at most Discord's 25 choices,
and performs zero disk or network I/O.

## Container

Build and run locally:

```bash
docker build --tag wiz-hatch:0.1.0 .
docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --env-file .env.example wiz-hatch:0.1.0
```

Supply `DISCORD_TOKEN` in a private env file rather than editing the checked-in
example. The image runs as UID/GID `10001`, writes no bytecode, and exposes
dependency-free liveness and readiness probes on port 8080.

## Automated k3s deployment

The CD workflow runs for every push to `main` on a self-hosted runner labeled
`self-hosted`, `Linux`, and `ARM64`. The runner needs:

- Docker with Buildx;
- `kubectl` configured for the target k3s cluster;
- permission to manage the `wiz-hatch` namespace, its Secret, and Deployment;
- network access to Discord, GitHub, GHCR, and Python package indexes.

Repository setup:

1. Create a protected GitHub environment named `production`.
2. Add `DISCORD_TOKEN` as an environment secret.
3. Require reviews for the environment and protect the `main` branch.
4. After the first publication, confirm the GHCR package is public so k3s can
   pull it without a registry credential.

The workflow builds a Linux ARM64 image, publishes commit, SemVer, and `latest`
tags to GHCR, emits a signed build-provenance attestation, reconciles the
Discord token through standard input, applies the hardened manifests, pins the
Deployment to the immutable commit tag, and waits for rollout completion.

For a manual first deployment, create the secret without writing it to disk:

```bash
printf 'DISCORD_TOKEN=%s\n' "$DISCORD_TOKEN" \
  | kubectl --namespace default create secret generic wiz-hatch \
      --from-env-file=/dev/stdin --dry-run=client --output=yaml \
  | kubectl apply --filename=-
kubectl apply --filename k8s/deployment.yaml
```

The namespace enforces Kubernetes' restricted Pod Security Standard. The Pod
runs non-root with all Linux capabilities dropped, no service-account token,
no privilege escalation, a read-only root filesystem, bounded `/tmp`, and
explicit CPU and memory limits.

## Release policy

The package and deployment are currently at `0.1.0`. Releases follow
Semantic Versioning. Keep `pyproject.toml`, `src/wiz_hatch/__init__.py`, the
Docker tag, image labels, and Kubernetes labels synchronized.

## Security and contributing

Do not open a public issue for a vulnerability. Follow [SECURITY.md](SECURITY.md)
for private reporting. Pull requests must pass formatting, linting, strict type
checking, tests on Python 3.11 through 3.13, and an ARM64 container build.

This project is available under the [MIT License](LICENSE).

