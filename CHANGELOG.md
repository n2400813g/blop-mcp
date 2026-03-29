# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) as described in [`docs/VERSIONING.md`](docs/VERSIONING.md).

## [Unreleased]

## [0.4.0] - 2026-03-29

### Added

- Customer-facing documentation: data handling, versioning policy, onboarding guide, support runbook.
- `SECURITY.md` for coordinated vulnerability reporting.
- Dependabot configuration for GitHub Actions and pip.
- CI gate: `pytest` (unit suite excluding slow, integration, and performance folders).

### Changed

- PyPI trove classifier: **Alpha → Beta** (`Development Status :: 4 - Beta`).

### Fixed

- (Packaging) Release metadata aligned with supported production baseline documented in `docs/production_setup.md`.

[Unreleased]: https://github.com/n2400813g/blop-mcp/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/n2400813g/blop-mcp/compare/v0.3.0...v0.4.0
