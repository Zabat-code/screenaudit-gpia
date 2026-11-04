# Architecture and verification notes

## Scope

This document describes the `gpia` package present in this repository. The root-level Python files are a separate legacy prototype; their historical configuration is deliberately omitted. Compatibility between both parts has not been independently verified in this publication staging environment.

## Components

- `__main__.py` invokes the command-line entry point in `cli.py`. The CLI implements commands including `init`, `doctor`, `selftest`, `capture`, `resume`, `control`, `health`, `bench` and autostart configuration. Review `python -m gpia --help` for the actual installed version before use.
- `config.py` merges, validates, loads and saves configuration. `paths.py` determines per-user paths and normalizes service names. There is a known issue with the configured `output.root`; the expected-failure test documents it.
- `backends/base.py` defines the backend interface and window-information type. `backends/factory.py` selects an implementation. `linux_x11.py` uses X11; `windows.py` uses Win32; `null.py` supplies synthetic behavior for testing. The null backend is not a desktop capture test.
- `idle.py` provides the idle gate. `policy.py` decides which applications are included or excluded. `recorder.py` coordinates session state, polling, policy, idle behavior, click events and recording.
- `video.py` constructs FFmpeg commands, manages video segments and exposes audio-stream checks. `schemas.py` defines the sample schema and JSONL writer.
- `persist.py` stores session state, identifies resumable sessions and includes segment-repair helpers. `control.py` implements the control channel and permission checks. These mechanisms require platform-specific security review and must not be assumed to be tamper-proof.
- `diagnostics.py` and `probes.py` implement checks and diagnostics. A skipped or mocked check is not proof that a real subsystem works.

## Execution flow

1. The command-line entry point parses the requested operation and loads configuration.
2. Capture selects a backend and performs applicable preflight checks.
3. The recording session samples window and activity data, evaluates policy and idle conditions, and coordinates video segments and event records.
4. Session state and logs support diagnostics and potential resumption; verify storage paths and access permissions before using real data.
5. Platform APIs and FFmpeg provide the actual capture. Failure modes depend on operating-system session, display server, access rights and installed dependencies.

## Outputs and privacy

Session files can contain screen recordings, process names, window titles, click activity, service identifiers and timestamps. Their contents must be treated as sensitive. Limit capture to explicitly authorized sessions and use visible, appropriate notice; define access control and retention outside this prototype. No actual session output or client configuration is included in this repository.

## Current evidence and remaining work

The publication staging copy completed 98 tests successfully with one expected failure on Linux. These are a mixture of unit and integration-style tests; they do not validate real Windows capture or a full user workday. A previous development run exercised synthetic X11/FFmpeg capture; that evidence is not included because session artifacts are excluded. Windows hooks, domain deployment, three-user installation, antivirus quarantine recovery, full reboot/resume, Wayland and production security remain unverified. The `output.root` expected failure must be fixed and retested.

Before any release, perform a focused security and privacy review, validate permissions and retention, and repeat the end-to-end scenarios on the intended platforms. Do not use test results as a substitute for informed authorization or local policy.
