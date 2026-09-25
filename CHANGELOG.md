# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.3] - 2026-09-25

### Fixes

- ollama isntallation


## [0.7.2] - 2026-09-25

### Fixes

- add debuggin in setup and runtime to see installation problems


## [0.7.1] - 2026-09-24

### Other

- Add prefetch parameter


## [0.7.0] - 2026-09-23

### Chores

- fresh start


## [0.6.1] - 2026-09-23

### Chores

- generate CHANGELOG.md from git-cliff at release time


### Documentation

- document Conventional Commits in CONTRIBUTING.md


### Features

- add Makefile for recurring local commands


## [0.6.0] - 2026-09-24

### Added

- First-run onboarding: the UI and browser open before the vendored Ollama
  download, with a setup overlay (`GET /api/setup-status`) and a web model
  picker when none are installed.
- Interactive terminal prompt (`Download llama3.2:3b now? [Y/n]`) only when
  stdin and stdout are real TTYs; non-interactive launches skip it so a
  double-click cannot hang on stdin.

### Changed

- Personas stay greyed out until their base model is in `GET /api/models`;
  choosing one can confirm a download. Terminal, web onboarding, and Settings
  all share that inventory.

## [0.5.0] - 2026-09-23

### Added

- `install.sh` as the single curl-pipe install path.
- Stable-named GitHub Release assets (`PortableAI-linux-x86_64.AppImage` and
  the same pattern on macOS/Windows) next to the versioned files.

## [0.1.5] - 2026-09-22

### Added

- `branding/generate_icons.py` as the single source for web, Linux, macOS,
  and iOS icons.
- Port-conflict fallback when 5050 or Ollama's 11434 is already taken.
- GitHub Releases as an update-check source.
- `PORTABLEAI_EXTERNAL_OLLAMA_URL` to skip the vendored Ollama.

### Fixed

- AppImage icon packaging.
- mDNS advertisement colliding when two instances share a host.

## [0.1.1] - 2026-09-21

### Added

- Initial public release: persona chat UI, LAN pairing, vendored Ollama on
  Linux/macOS, AppImage packaging, and GitHub Releases.
