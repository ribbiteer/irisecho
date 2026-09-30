# Contributing

Thanks for helping. A few ground rules keep the project legally clean and
keep contributors' privacy intact.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and git.

```sh
python scripts/dev_setup.py   # enables the repo's git hooks
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check
```

## Sign your commits off (DCO)

Every commit needs a `Signed-off-by:` line, added by `git commit -s`. It
certifies the [Developer Certificate of Origin 1.1](https://developercertificate.org/):
that you wrote the change, or otherwise have the right to submit it under
this project's license (AGPL-3.0-or-later). There is no CLA.

## Keep identities out of the repo

The hooks installed by `dev_setup.py` block a commit that contains:

- a commit or sign-off email that is not a GitHub `noreply` address
- home-directory paths, private LAN addresses, default machine hostnames
- access tokens
- image metadata. ComfyUI embeds its full workflow, file paths included, in
  every PNG it saves, so strip metadata from screenshots and samples first.

You can also keep a private denylist of your own identifying strings at
`~/.config/irisecho/identity-denylist.txt`, outside the repository. The hooks
use it and never print what matched unless you pass `--show`.

For a deliberate, harmless match, add `identity-scan: allow` to that line.

## Models and dependencies

- Never commit model weights or generated outputs.
- A new dependency or model needs its license checked and recorded in
  `THIRD_PARTY_NOTICES.md` or `MODELS.md` in the same pull request. Code must
  be compatible with AGPL-3.0. Weights may use any license, but
  non-commercial and gated ones must be opt-in and labelled.
