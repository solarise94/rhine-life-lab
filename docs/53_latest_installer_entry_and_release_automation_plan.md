# Latest Installer Entry And Release Automation Plan

## Goal

Define a product-facing release protocol that supports a true "latest"
installer entrypoint for end users while keeping the existing versioned release
artifact model.

Target end-user UX:

```bash
curl -fsSL \
  https://github.com/solarise94/RhineDataLab/releases/latest/download/install.sh | \
  bash
```

Target operator fallback UX:

```bash
VERSION=0.4.2
curl -fsSL \
  https://github.com/solarise94/RhineDataLab/releases/download/v${VERSION}/install.sh | \
  bash
```

Target low-level artifact UX:

```bash
bash blueprint-re-<version>-linux-x86_64.sh
```

This document does not change the installer runtime model. It defines the
release-distribution layer that makes the install path product-like.

Important status note:

- this document describes the target release protocol
- the repository is still in the Phase 1 transition state
- public docs should not claim `releases/latest/download/install.sh` as the
  default product entrypoint until Phase 2/3 are actually implemented

## Why This Is Needed

The current user-mode installer plan already separates:

- a small text-only downloader stub
- a versioned self-extracting installer artifact

That is the correct shape, but the public release interface is still not ideal
for end users because it expects a versioned asset name such as:

```text
blueprint-re-0.4.1-linux-x86_64.sh
```

This creates three problems:

- the GitHub-facing README cannot show a clean "latest" install command
- users must manually discover and copy a version number
- upgrade instructions drift toward "download this exact file again" instead of
  "rerun the installer entrypoint"

The missing piece is not the installer itself. The missing piece is a stable
release entrypoint protocol.

## Product Decision

Blueprint RE should expose two public installer layers:

- stable public entrypoint with a fixed filename
- versioned authoritative installer artifact

Recommended public assets per release:

```text
install.sh
install.sh.sha256
blueprint-re-<version>-linux-x86_64.sh
blueprint-re-<version>-linux-x86_64.sh.sha256
```

Optional future assets:

```text
install_linux_aarch64.sh
install_linux_aarch64.sh.sha256
latest.json
release-manifest.json
```

The fixed-name `install.sh` asset is the end-user entrypoint. The
versioned `blueprint-re-<version>-linux-x86_64.sh` artifact remains the real
self-extracting installer bundle.

## Branding And Naming Transition

The repository has moved to `RhineDataLab`. Future release conventions should
align the public product surface with `莱茵数据实验室（RhineDataLab）` instead
of keeping `blueprint-re` as the long-term user-facing brand.

Branding targets for future tagged releases:

- GitHub repository path should use `solarise94/RhineDataLab`
- release notes and README install commands should reference
  `莱茵数据实验室（RhineDataLab）`
- the web UI logo and visible product name should read
  `莱茵数据实验室（RhineDataLab）`
- installer-facing artifact naming should gradually move away from
  `blueprint-re-*`

Recommended naming end state:

```text
install.sh
install.sh.sha256
rhinedatalab-<version>-linux-x86_64.sh
rhinedatalab-<version>-linux-x86_64.sh.sha256
```

The fixed-name `install.sh` entrypoint remains stable across the rename. The
main migration is the authoritative versioned installer filename and the
visible product branding.

### Compatibility Policy

The rename should be treated as a release-contract migration, not a one-shot
flag day.

Recommended compatibility window:

- keep `install.sh` stable throughout the rename
- for one or two tagged releases, publish both:
  - `blueprint-re-<version>-linux-x86_64.sh`
  - `rhinedatalab-<version>-linux-x86_64.sh`
- during that window, the two installer files should be byte-identical copies
  of the same release artifact
- each published filename should have its own matching `.sha256` file
- the generated `install.sh` should prefer the `rhinedatalab-*` filename once
  that name exists in the release
- update README and release notes to prefer `RhineDataLab` naming
- after the compatibility window, remove the old `blueprint-re-*` asset names

This keeps rollback, mirrors, and existing scripts from breaking while the
public product identity shifts to `莱茵数据实验室（RhineDataLab）`.

### Scope Of Rename

The rename should eventually cover:

- GitHub repository name
- release asset filenames
- download URLs used in public documentation
- UI logo and visible product title
- installer and release notes wording

The rename does not need to force an immediate change to every internal path,
systemd unit, or compatibility identifier on day one. Internal names can lag
behind the public brand if that lowers migration risk.

## Recommended Architecture

### Layer 1: Public Entry Script

`install.sh` should be a small text-only downloader/bootstrap script.

Responsibilities:

- detect Linux architecture
- resolve the correct versioned installer for this release
- download the versioned installer to a temporary file
- download and verify the matching checksum
- execute the installer and forward CLI arguments
- clean up temporary files

This script should remain safe for `curl | bash`. Layer 1 is the only layer
that should ever be piped into Bash.

### Layer 2: Versioned Installer Artifact

`blueprint-re-<version>-linux-x86_64.sh` should remain the authoritative
self-extracting installer.

Responsibilities:

- extract payload
- validate payload metadata
- create or reuse runtime environment
- install backend/frontend/manager-agent/nginx stack
- generate user-mode systemd units
- switch releases and support rollback

This artifact should not be piped into Bash directly.
Layer 2 must be downloaded as a normal file and then executed.

### Layer 3: Release Automation

Release automation should publish both layers in one release transaction.

Minimum outcome for every tagged release:

- create versioned self-extracting installer artifact
- create checksum for the versioned artifact
- create fixed-name `install.sh` asset for that same release
- create checksum for `install.sh`
- upload all assets to the GitHub Release
- verify that `releases/latest/download/install.sh` resolves after publish

## Why Fixed Filenames Matter

GitHub `releases/latest/download/...` works best when the asset name is stable.

It is a poor fit for filenames that embed the version, because the README then
has to teach the user to interpolate:

```bash
VERSION=0.4.1
.../releases/download/v${VERSION}/blueprint-re-${VERSION}-linux-x86_64.sh
```

That is acceptable for operators and CI. It is not ideal as the primary
end-user install path.

A fixed asset name solves this cleanly:

```bash
curl -fsSL \
  https://github.com/solarise94/RhineDataLab/releases/latest/download/install.sh | \
  bash
```

## Release Asset Contract

For Linux x86_64, each tagged GitHub Release should contain:

```text
install.sh
install.sh.sha256
blueprint-re-<version>-linux-x86_64.sh
blueprint-re-<version>-linux-x86_64.sh.sha256
```

Behavior contract:

- `install.sh` is always text-only
- `install.sh` always downloads a matching versioned installer from the same release
- `install.sh.sha256` verifies only the downloader asset itself
- `blueprint-re-<version>-linux-x86_64.sh.sha256` verifies the self-extracting installer
- the downloader must fail closed when checksum verification fails
- if `--keep-installer` is passed, the downloader should preserve the downloaded
  versioned installer locally for reuse, audit, or rollback workflows

Recommended follow-up contract:

- `install.sh` should embed the release version it belongs to
- `install.sh` should not depend on a mutable external `latest.json` for the
  primary GitHub Releases path

That keeps a tagged release self-contained and auditable.

## Version Resolution Strategy

There are two viable strategies.

### Option A: Fixed Asset Per Release

Every GitHub Release uploads an `install.sh` asset that already knows which
versioned artifact to fetch from that same release.

Pros:

- simplest user mental model
- no extra metadata endpoint required
- works naturally with `releases/latest/download/install.sh`
- easier to audit and mirror

Cons:

- release tooling must rewrite or template the downloader per release

### Option B: External Metadata Resolution

Publish a stable downloader script plus a `latest.json` endpoint. The downloader
resolves the current version and then downloads the matching artifact.

Pros:

- one downloader script can serve many versions
- flexible channel support such as `stable`, `latest`, `dev`

Cons:

- adds a second mutable control plane
- increases failure modes
- makes GitHub Releases less self-contained
- README still depends on external metadata being published correctly

## Recommended Choice

Choose Option A for the primary GitHub product path.

Use:

- fixed-name `install.sh` on every tagged release
- fixed-name `install.sh.sha256` on every tagged release
- versioned self-extracting installer as the actual payload carrier

Keep `latest.json` as an optional secondary mechanism for:

- internal mirrors
- non-GitHub distribution
- future channel semantics such as `dev` or `nightly`

Do not make `latest.json` a prerequisite for the default GitHub install path.

## Required Repository Changes

This is not a README-only change. The repository and release process need to
support the new entrypoint contract.

### Must Change

- release build tooling must emit a public downloader artifact with a fixed name
- release publish tooling must upload both fixed-name and versioned assets
- downloader default URLs must stop using placeholder domains for the GitHub path
- checksum publication must exist for the fixed-name downloader asset
- release naming and public assets must follow the `RhineDataLab` repository
  and the `莱茵数据实验室` public brand

### Likely Change

- `README.md` should use `releases/latest/download/install.sh | bash` as the
  primary user install command under the `RhineDataLab` repository path
- `docs/README.md` and `docs/for_agent_install.md` should align to the same
  public entrypoint
- release tests should verify both the fixed-name entrypoint and the versioned
  installer path
- UI-facing release assets should switch visible naming and logo treatment to
  `莱茵数据实验室（RhineDataLab）`

### Does Not Need To Change

- self-extracting installer payload structure
- backend wheel bundling model
- frontend standalone bundling model
- manager-agent dependency bundling model
- user-mode systemd deployment model

## Release Automation Should Be Automated

Yes. This should be fully automated.

Manual release publication is the wrong long-term model because the install
entrypoint becomes part of the product contract. That contract should not
depend on hand-uploaded filenames or human memory.

Release automation should:

- build the backend wheel and dependency wheels
- build the frontend standalone bundle
- bundle manager-agent production dependencies
- create the versioned release tarball
- create the versioned self-extracting installer
- generate `install.sh` from a template with release-specific URLs
- publish the correct public-facing `莱茵数据实验室（RhineDataLab）` branding
  assets and labels
- generate checksums for both `install.sh` and the versioned installer
- upload all release assets to GitHub Releases
- run post-publish validation against the release URLs

## Proposed Automation Pipeline

Recommended pipeline for a tagged release:

1. validate working tree and version metadata
2. build release bundle tarball
3. build versioned self-extracting installer
4. render release-specific `install.sh`
5. generate checksums for both public assets
6. create or update GitHub Release for the tag
7. upload all release assets
8. validate `releases/latest/download/install.sh`
9. validate versioned installer URL
10. validate checksum files match uploaded artifacts

## Recommended Scripts

Suggested local script responsibilities:

### `scripts/build_release_bundle.sh`

Continue to produce the versioned payload tarball.

### `scripts/build_self_extracting_installer.sh`

Continue to produce:

```text
blueprint-re-<version>-linux-x86_64.sh
```

During the branding transition this script should support dual output or a
configurable artifact prefix so release automation can publish both legacy and
`RhineDataLab`-branded installer names during the compatibility window.

### `scripts/render_release_downloader.sh`

New script.

Responsibilities:

- render `install.sh` from a template
- bake in release-specific artifact URLs
- bake in expected downloader metadata such as target version and architecture
- avoid placeholder base URLs for GitHub Releases

### `scripts/publish_release.sh`

New script or CI job wrapper.

Responsibilities:

- create GitHub Release assets
- upload `install.sh`
- upload `install.sh.sha256`
- upload `blueprint-re-<version>-linux-x86_64.sh`
- upload `blueprint-re-<version>-linux-x86_64.sh.sha256`
- when dual-publishing is enabled, also upload:
  - `rhinedatalab-<version>-linux-x86_64.sh`
  - `rhinedatalab-<version>-linux-x86_64.sh.sha256`
- validate uploaded assets

## Downloader Template Contract

The generated public `install.sh` should:

- be pure text Bash
- hardcode the exact artifact URL for its paired release
- hardcode the exact checksum URL for the paired versioned installer
- remain architecture-aware if multiple architectures are published
- support forwarding flags such as `--offline`, `--rollback`, and `--keep-installer`
- fail closed when checksum download or checksum verification fails

It should not:

- resolve arbitrary channels by default for the GitHub path
- use `example.com` placeholder URLs in a release build
- stream binary payloads directly into Bash

## Validation Requirements

Release automation is incomplete unless it validates the public URLs after
publish.

Minimum checks:

- `curl -fsSL https://github.com/.../releases/latest/download/install.sh` returns a script
- `bash install.sh --help` succeeds
- `install.sh.sha256` matches the uploaded `install.sh`
- `blueprint-re-<version>-linux-x86_64.sh.sha256` matches the uploaded installer
- if dual-publishing is enabled, `rhinedatalab-<version>-linux-x86_64.sh.sha256`
  also matches the uploaded installer

Recommended smoke checks:

- run downloader in a disposable environment and confirm it reaches installer launch
- confirm checksum failure is fail-closed
- confirm explicit version install path still works

## Documentation Changes Once Implemented

When this release protocol is implemented, the public docs should converge on:

### Default user install

```bash
curl -fsSL \
  https://github.com/solarise94/RhineDataLab/releases/latest/download/install.sh | \
  bash
```

### Upgrade to latest

```bash
curl -fsSL \
  https://github.com/solarise94/RhineDataLab/releases/latest/download/install.sh | \
  bash
```

### Install or upgrade to an exact version

```bash
VERSION=0.4.2
curl -fsSL \
  https://github.com/solarise94/RhineDataLab/releases/download/v${VERSION}/install.sh | \
  bash
```

### Rollback

```bash
curl -fsSL \
  https://github.com/solarise94/RhineDataLab/releases/latest/download/install.sh | \
  bash -s -- --rollback <previous-version>
```

Rollback still requires that `<previous-version>` already exists under the
local release cache, typically:

```text
~/.local/share/blueprint-re/releases/<previous-version>/
```

If the target version is not already present locally, rollback must fail
explicitly. During the transition period, the preferred operator guidance is to
use any already downloaded installer or an explicit versioned installer URL to
trigger rollback, rather than implying that a fresh machine can "roll back" to
a version it has never installed.

This keeps one user-facing mental model:

- first install: run the installer entrypoint
- upgrade: run the installer entrypoint again
- rollback: run the installer entrypoint with `--rollback`

## Phased Rollout

### Phase 1

- keep current versioned installer flow
- document the target fixed-entrypoint model
- do not promise `latest/download/install.sh` yet
- README should continue to recommend the versioned self-extracting installer
  as the stable product path
- if `scripts/install_downloader.sh` is documented, it should be labeled as a
  transitional convenience path rather than the final product entrypoint

### Phase 2

- add downloader rendering script
- add release automation that uploads fixed-name assets
- validate GitHub Release latest entrypoint

### Phase 3

- switch `README.md` to fixed latest entrypoint
- demote versioned install commands to advanced usage

## Open Questions

1. Should Linux ARM64 ship the same day as x86_64 fixed-entry assets, or later?
2. Should we publish only GitHub Release assets first, or also maintain a mirror endpoint?

## Recommendation Summary

- yes, the product should support a true `latest` install command
- yes, this requires repository and release-process changes, not only README edits
- yes, release publication should be automated
- the cleanest model is a fixed-name `install.sh` downloader plus a versioned
  self-extracting installer artifact
- GitHub Releases should be the primary stable distribution surface for v1
- the public-facing repository, release assets, and visible product branding
  should converge on `莱茵数据实验室（RhineDataLab）`
