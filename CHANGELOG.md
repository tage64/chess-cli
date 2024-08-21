# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Make it possible to select a file with a traditional "Open" or "Save As" dialog by providing the
  `--dialog` (or `-d`) flag to the `load` and `save` commands.  
- Remove the `-f` flag from the `load` and `save` commands. A file path may be provided as a
  positional argument (with no flags) instead. This is the old behaviour prior to v0.4.0.
- Add recording status to the prompt: If a recording is in progress or paused, the string
  `(recording)` or `recording paused)` will be added to the prompt respectively.
- The subcommand `record positions` to get a list of all recorded positions so far.
- The `rec` alias for the `record` command.
- The following aliases for the subcommands of the `record` command:
  - `s` and `st` for `start`
  - `m` for `mark`
  - `d` and `del` for `delete
- Print "Mark set" when using the `record mark` command.

## [0.5.0] - 2024-08-19

### Added

- The `s` command to scan files, ranks and diagonals on the board.
- The `p` command to quickly get the location of all pieces of a certain type.
- The `at` command to quickly get attackers of a certain square.

### Changed

- The shorthand `p` for the `play` command has been changed to `pl`.
- The shorthand `pl` for the `player` command has been changed to `plr`.

[unreleased]: https://github.com/tage64/chess-cli/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/tage64/chess-cli/compare/v0.5.0...v0.4.2
