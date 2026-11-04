# ScreenAudit — capture and activity analysis

ScreenAudit contains a legacy local capture and video-analysis prototype and a newer cross-platform capture layer in `gpia/`. This is **experimental software**, not a production-ready employee-monitoring system. Deploy it only with appropriate notice, authorization, access controls and retention policies. Captured screens and activity metadata may contain sensitive information.

## Project layout

- `gpia/`: newer capture layer, command-line entry point, configuration, session lifecycle, recording, idle detection and platform backends.
- `gpia/backends/`: Linux/X11, Windows and a null backend for tests.
- `tests/`: unit and integration-style tests, including simulated Windows behavior.
- `requirements.txt`: dependencies for the newer layer and its tests.
- Root-level Python files: historical prototype; kept for reference and not integrated into the newer package.

## Status and limitations

The newer layer targets Linux/X11 and Windows and relies on FFmpeg for video recording. Linux/X11 automated tests and synthetic capture have been exercised in development; that does not prove production desktop capture. Windows behavior has unit tests using mocks but has **not been validated in a real Windows session**. Wayland, domain deployment, multi-user installation and recovery following antivirus quarantine have not been validated. See the code and tests for the exact behavior exercised.

A known expected-failure test records that the configured output root is not consistently honored during session creation. Do not deploy sensitive recordings until this is corrected and verified. Recordings, generated session data and real configuration are intentionally not included. The older root-level prototype may refer to a client-specific configuration that is intentionally absent; it is not a ready-to-run legacy release.

## Run tests

```bash
python -m venv .venv
# Activate the virtual environment using your platform's standard command.
python -m pip install -r requirements.txt
python -m pytest -q
```

The sanitized staging copy was tested on Linux: **98 passed, 1 expected failure**. This is not a Windows runtime test and does not establish security or production readiness. The project has no license grant in this repository; obtain permission before redistributing or reusing it.

## Data handling

Do not commit recordings, screenshots, credentials, logs containing user data, real client configuration or personal information. Review capture notices and configuration, access controls and retention before any use. No configuration file from a real client is distributed in this repository.
