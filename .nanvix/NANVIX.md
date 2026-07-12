# libffi Port for Nanvix

> **TL;DR:** This is a port of the libffi foreign function interface library for the Nanvix operating system. Jump to [Quick Start](#quick-start) to get started immediately.

---

## Overview

This document describes the port of [libffi](https://sourceware.org/libffi/) for the [Nanvix](https://github.com/nanvix/nanvix) operating system. This port enables libffi to run on Nanvix, a POSIX-compatible educational operating system.

| Property | Value |
|----------|-------|
| **Base Version** | libffi 3.4.6 |
| **Target Platform** | Nanvix (i686) |
| **Build System** | GNU Make (wrapping autotools configure) |
| **Compiler** | Clang/LLVM from the Nanvix C SDK |

**What's included:**
- ✅ Cross-compilation support for Nanvix
- ✅ Static library build (`libffi.a`)
- ✅ Test executable (`ffi_test.elf`)
- ✅ Build helper scripts
- ✅ CI/CD integration

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Prerequisites](#prerequisites)
3. [Building](#building)
4. [Testing](#testing)
5. [Changes Summary](#changes-summary)
6. [Known Limitations](#known-limitations)
7. [CI/CD](#cicd)

---

## Quick Start

For experienced users who want to build quickly:

```bash
# 1. Configure the immutable SDK and download the matching runtime.
SDK=ghcr.io/nanvix/nanvix-sdk-c-clang@sha256:f61737cb0780e6a2058c6d0bdf8ae5562db18de437173b2bcbbe6973abd3689f
./z setup --with-docker "$SDK"

# 2. Build.
./z build

# 3. Run tests.
./z test
```

The wrapper bootstraps the pinned `nanvix-zutil` release automatically.
The SDK contains the compiler, target headers, libc, startup object, linker
script, and compiler runtime. `./z setup` downloads only the Nanvix runtime
binaries used by tests.

To inspect or use the SDK directly:

```bash
docker pull "$SDK"
docker run --rm "$SDK" cat /opt/nanvix/nanvix-sdk.json
```

Continue reading for detailed instructions.

---

## Prerequisites

You need the following to build libffi for Nanvix:

| Component | Description | Install |
|-----------|-------------|---------|
| **nanvix-zutil** | Build orchestration CLI | `pip install` from [GitHub Releases](https://github.com/nanvix/zutils/releases) |
| **Nanvix C SDK** | Clang/LLVM and target build sysroot | Immutable Docker image |
| **Nanvix Runtime** | Kernel, daemons, and image tools | `./z setup` |

### Available Platform Configurations

| Platform | Process Mode | Artifact Pattern |
|----------|--------------|------------------|
| microvm | standalone | `microvm.*standalone` |

The 0.20.0 release publishes only microvm runtime assets and does not publish
a standalone 128 MB artifact. Consequently, the manifest and active CI
workflows use microvm at 256 MB; this is a runtime compatibility constraint,
not a libffi port failure. All existing Linux functional and Windows test
types remain enabled.

### Downloading Nanvix

```bash
curl -fsSL https://raw.githubusercontent.com/nanvix/nanvix/refs/heads/dev/scripts/get-nanvix.sh | bash -s -- nanvix-artifacts
```

The script downloads all release artifacts. Extract the one matching your target platform (see [Quick Start](#quick-start) for a complete example).

---

## Building

### Using nanvix-zutil (Recommended)

```bash
# Configure the SDK, download runtime binaries, and build.
SDK=ghcr.io/nanvix/nanvix-sdk-c-clang@sha256:f61737cb0780e6a2058c6d0bdf8ae5562db18de437173b2bcbbe6973abd3689f
./z setup --with-docker "$SDK"
./z build
```

### SDK Contents

The build uses SDK v0.20.0-sdk.1, which targets Nanvix runtime 0.20.0. Its
Clang driver selects `i686-unknown-nanvix` and supplies `crt0.o`, `user.ld`,
libc, libm, and compiler-rt automatically. Final executable links also use
Clang; the port does not manually compose Newlib, libgcc, or Nanvix runtime
libraries.

The SDK includes Autoconf, Automake, libtool, M4, GCC, and Perl. It does not
include Texinfo. This port regenerates the autotools files successfully without
`makeinfo` and configures libffi with `--disable-docs`, so no derived image is
needed.

### Using Native Toolchain

```bash
export NANVIX_TOOLCHAIN=/path/to/sdk      # nanvix-sdk.json and bin/clang
export NANVIX_HOME=/path/to/runtime       # runtime binaries used by tests
make -f Makefile.nanvix \
  PLATFORM=microvm PROCESS_MODE=standalone MEMORY_SIZE=256mb \
  NANVIX_ROOT="$PWD/.nanvix" OUT_DIR="$PWD/.nanvix/out" \
  DIST_DIR="$PWD/.nanvix/out/dist" LIB_OUT="$PWD/.nanvix/out/lib" \
  INCLUDE_OUT="$PWD/.nanvix/out/include" TEST_OUT="$PWD/.nanvix/out/test"
```

### Build Outputs

After a successful build, you will have:

| File | Description |
|------|-------------|
| `libffi.a` | libffi static library |
| `ffi_test.elf` | Minimal FFI call test executable |

---

## Testing

> **Important:** Functional tests must be run through the Nanvix daemon (`nanvixd.elf`).

### Running the Test Suite

```bash
# Run all tests
./z test
```

Alternatively, invoke Make directly:

```bash
make -f Makefile.nanvix CONFIG_NANVIX=y NANVIX_HOME=/path/to/nanvix test
```

### Test Levels

| Target | Description |
|--------|-------------|
| `test-functional` | Builds and runs a minimal FFI call test via `nanvixd.elf` |
| `test` | Runs the functional test suite |

---

## Changes Summary

The following changes were made to support Nanvix.

### Build System Changes

| Change | Description |
|--------|-------------|
| New Makefile | Added `Makefile.nanvix` for Nanvix cross-compilation |
| Cross-compilation | Uses `CONFIG_NANVIX=y` option to enable Nanvix build |
| Docker support | Immutable SDK image selected by `./z setup --with-docker` |
| Configure wrapper | Wraps `./configure` with Nanvix cross-compilation settings |
| Shared libraries | Disabled (`--disable-shared --enable-static`) |
| Platform patch | Patches `config.sub` to recognize `nanvix` as a valid platform |

### New Files

| File | Purpose |
|------|---------|
| `Makefile.nanvix` | Standalone Makefile for Nanvix cross-compilation |
| `NANVIX.md` | This documentation file |
| `z` | Unified entry point (delegates to `z.sh` or `z.ps1`) |
| `z.sh` | Bash wrapper that delegates to `nanvix-zutil` CLI |
| `z.ps1` | PowerShell wrapper that delegates to `nanvix-zutil` CLI |
| `.nanvix/z.py` | Build script (extends `nanvix-zutil` `ZScript`) |
| `.nanvix/nanvix.toml` | Package manifest for dependency resolution |
| `.github/workflows/nanvix-ci.yml` | CI workflow for automated builds |

---

## Known Limitations

| Limitation | Impact |
|------------|--------|
| **No shared libraries** | Only static library (`libffi.a`) is built |
| **Static linking only** | All executables are statically linked |
| **No closures** | FFI closures depend on writable+executable memory |

---

## CI/CD

The GitHub Actions workflow at `.github/workflows/nanvix-ci.yml` automates building and testing on every change. It uses the `nanvix-zutil` CLI (installed from the wheel in GitHub Releases) for all build orchestration.

### Workflow Structure

| Job | Description |
|-----|-------------|
| `ci` | Calls the shared Nanvix reusable workflow that builds, tests, and packages this port |

The `ci` job delegates to a central reusable workflow, which defines internal jobs such as
`get-nanvix-info`, `build`, `release`, and `report-failure` to handle manifest resolution,
cross-compilation, release creation, and failure reporting. These internal jobs live in the
shared CI configuration and are not defined directly in this repository's workflow file.

### Trigger Events

| Event | Description |
|-------|-------------|
| Push to `nanvix/**` | Any push to Nanvix branches |
| PR to `nanvix/**` | Pull requests targeting Nanvix branches |
| Daily schedule | Runs at midnight UTC |
| Manual dispatch | Can be triggered manually |

### Build Matrix

The CI runs the standalone deployment mode on microvm at 256 MB on Linux
(build + full test), plus the corresponding standalone Windows test:

#### Linux (build + full test)

| Platform | Process Mode |
|----------|--------------|
| microvm | standalone |

#### Windows (standalone test)

| Platform | Process Mode |
|----------|--------------|
| microvm | standalone |

> **Note:** Only the standalone deployment mode is supported. Single-process
> and multi-process modes have been dropped. Windows tests run the standalone
> deployment mode using `nanvixd.exe`.

All configurations run in parallel with `fail-fast: false`, ensuring that all platforms are tested even if one fails.

---
