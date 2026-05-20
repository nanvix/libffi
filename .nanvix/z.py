# pyright: basic
# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Nanvix build script for libffi.

Usage:
    ./z setup     # Download Nanvix sysroot
    ./z build     # Cross-compile libffi.a
    ./z test      # Run test suite (smoke + integration + functional)
    ./z release   # Package release tarball
    ./z clean     # Remove build artifacts
"""

import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from nanvix_zutil import (
    CFG_SYSROOT,
    EXIT_MISSING_DEP,
    TOOLCHAIN_CONTAINER_PATH,
    ZScript,
    log,
    make_initrd,
    run,
)

# Makefile variable names (build-system-specific).
_MAKE_VAR_HOME = "NANVIX_HOME"
_MAKE_VAR_TOOLCHAIN = "NANVIX_TOOLCHAIN"
_MAKE_VAR_PLATFORM = "PLATFORM"
_MAKE_VAR_PROCESS_MODE = "PROCESS_MODE"
_MAKE_VAR_MEMORY_SIZE = "MEMORY_SIZE"

_LIBFFI_VERSION = "3.4.6"
_LIBFFI_TARBALL_URL = (
    f"https://github.com/libffi/libffi/releases/download/v{_LIBFFI_VERSION}/"
    f"libffi-{_LIBFFI_VERSION}.tar.gz"
)


IS_WINDOWS = sys.platform == "win32"


class LibffiBuild(ZScript):
    """Build script for nanvix/libffi."""

    def _make_args(self, *targets: str) -> list[str]:
        """Build the common make argument list."""
        sysroot = self.config.get(CFG_SYSROOT, "")
        if not sysroot:
            log.fatal(
                f"{CFG_SYSROOT} is not set.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first to download the sysroot.",
            )
        toolchain_p = str(TOOLCHAIN_CONTAINER_PATH)
        sysroot_p = (
            self.docker.translate_path(Path(sysroot)) if self.docker else Path(sysroot)
        )

        args = [
            "make",
            "-f",
            "Makefile.nanvix",
            f"{_MAKE_VAR_HOME}={sysroot_p}",
            f"{_MAKE_VAR_TOOLCHAIN}={toolchain_p}",
        ]

        args.extend(
            [
                f"{_MAKE_VAR_PLATFORM}={self.config.machine}",
                f"{_MAKE_VAR_PROCESS_MODE}={self.config.deployment_mode}",
                f"{_MAKE_VAR_MEMORY_SIZE}={self.config.memory_size}",
            ]
        )

        args.extend(targets)
        return args

    def _ensure_configure(self) -> None:
        """Download the release tarball if ``./configure`` is missing.

        Git checkouts of libffi do not include the autotools-generated
        ``configure`` script.  When it is absent we fetch the official
        release tarball and overlay it onto the working tree.
        """
        configure = self.repo_root / "configure"
        if configure.exists():
            return

        log.info("configure script not found — downloading release tarball")
        with TemporaryDirectory() as tmp:
            tarball = Path(tmp) / f"libffi-{_LIBFFI_VERSION}.tar.gz"
            urllib.request.urlretrieve(_LIBFFI_TARBALL_URL, tarball)
            with tarfile.open(tarball, "r:gz") as tf:
                if sys.version_info >= (3, 12):
                    tf.extractall(tmp, filter="data")
                else:
                    tf.extractall(tmp)  # noqa: S202
            extracted = Path(tmp) / f"libffi-{_LIBFFI_VERSION}"
            for item in extracted.iterdir():
                dest = self.repo_root / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
        log.info("configure script ready")

    def setup(self) -> bool:
        """Download the Nanvix sysroot and prepare autotools sources."""
        ok = super().setup()
        self._ensure_configure()
        return ok

    def build(self) -> None:
        """Cross-compile libffi.a and ffi_test.elf for Nanvix.

        Both the library and the functional-test binary are produced
        here, where Docker is available. The test step then just runs
        the pre-built binary natively.
        """
        run(*self._make_args("all"), cwd=self.repo_root, docker=self.docker)

    def test(self) -> None:
        """Run the test suite.

        Smoke and integration tests are always delegated to the Makefile.
        The functional test in standalone mode is handled in Python via
        make_initrd so that initrd creation is shared across platforms.
        """
        if IS_WINDOWS:
            self._run_tests_windows()
            return

        if self.config.deployment_mode == "standalone":
            # Smoke + integration via Makefile (native), functional via Python.
            run(
                *self._make_args("test-smoke", "test-integration"),
                cwd=self.repo_root,
            )
            self._run_functional_standalone()
        else:
            targets = self.targets if self.targets else ["test"]
            # Timeout bounds the functional run (Makefile no longer relies on
            # the non-portable `timeout --foreground` binary).
            run(
                *self._make_args(*targets),
                cwd=self.repo_root,
                timeout=120,
            )

    def _run_functional_standalone(self) -> None:
        """Run the standalone functional test using make_initrd.

        Creates an initrd bundling ffi_test.elf with system daemons via
        make_initrd, and a ramfs providing /tmp for test file output.
        Assumes ``ffi_test.elf`` was produced by ``./z build``.
        """
        ffi_test_elf = self.repo_root / "ffi_test.elf"
        if not ffi_test_elf.is_file():
            log.fatal(
                "ffi_test.elf not found.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z build` first.",
            )

        print("=== libffi functional tests ===")
        print("  Running ffi_test.elf via nanvixd standalone...")

        sysroot = self.config.get(CFG_SYSROOT, "")
        sysroot_path = Path(sysroot)
        mkramfs = sysroot_path / "bin" / "mkramfs.elf"

        # Bundle ffi_test.elf + daemons into an initrd.
        initrd = make_initrd(self, "ffi_test.elf")

        try:
            with tempfile.TemporaryDirectory(prefix="nanvix_ffi_") as tmpdir:
                tmpdir_path = Path(tmpdir)
                ramfs_dir = tmpdir_path / "ramfs"
                ramfs_dir.mkdir()
                (ramfs_dir / "tmp").mkdir(exist_ok=True)
                ramfs_img = tmpdir_path / "rootfs.img"

                run(
                    str(mkramfs),
                    "-o",
                    str(ramfs_img),
                    str(ramfs_dir),
                )

                run(
                    str(sysroot_path / "bin" / "nanvixd.elf"),
                    "-bin-dir",
                    str(sysroot_path / "bin"),
                    "-ramfs",
                    str(ramfs_img),
                    "--",
                    str(initrd),
                    timeout=120,
                )
        finally:
            if initrd.exists():
                initrd.unlink()

        print("  PASS: ffi_test standalone (exit code 0)")
        print("  PASS: libffi functional tests")
        print("=== All libffi tests PASSED ===")

    def _run_tests_windows(self) -> None:
        """Run tests natively on Windows using nanvixd.exe.

        Only standalone mode is tested on Windows; multi-process and
        single-process require linuxd, which is Linux-only.  Uses
        make_initrd to bundle each test binary with system daemons,
        and a ramfs providing /tmp for any test I/O.
        """
        if self.config.deployment_mode != "standalone":
            print(
                f"Skipping tests on Windows for mode"
                f" '{self.config.deployment_mode}' (requires linuxd)."
            )
            return

        sysroot = self.config.get(CFG_SYSROOT, "")
        if not sysroot:
            log.fatal(
                f"{CFG_SYSROOT} is not set.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first.",
            )
        sysroot_path = Path(sysroot)
        nanvixd = sysroot_path / "bin" / "nanvixd.exe"
        mkramfs = sysroot_path / "bin" / "mkramfs.exe"
        if not nanvixd.is_file():
            log.fatal(
                "nanvixd.exe not found.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first.",
            )
        if not mkramfs.is_file():
            log.fatal(
                "mkramfs.exe not found.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first.",
            )

        # The Makefile outputs ffi_test.elf to the repo root.  Search
        # there first, then fall back to build/ for forward-compat.
        test_allowlist = {"ffi_test.elf"}
        test_binaries: list[Path] = []
        for candidate in [self.repo_root, self.repo_root / "build"]:
            if candidate.is_dir():
                elfs = sorted(candidate.glob("*.elf"))
                found = [b for b in elfs if b.name in test_allowlist]
                for b in found:
                    if b.name not in {x.name for x in test_binaries}:
                        test_binaries.append(b)

        if not test_binaries:
            expected = ", ".join(sorted(test_allowlist))
            log.fatal(
                f"No allowlisted test binaries found." f" Expected: {expected}.",
                code=EXIT_MISSING_DEP,
                hint=(
                    "Build the test binaries first"
                    " (run the Linux CI build)"
                    " and then rerun `./z test`."
                ),
            )

        failed: list[str] = []
        for binary in test_binaries:
            name = binary.stem
            print(f"RUN  {name}...")
            # make_initrd resolves binaries relative to repo_root;
            # copy the ELF there temporarily unless it already lives there.
            repo_elf = self.repo_root / binary.name
            copied_elf = False
            initrd: Path | None = None
            try:
                if binary.resolve() != repo_elf.resolve():
                    if repo_elf.exists():
                        raise FileExistsError(
                            f"refusing to clobber existing {repo_elf}"
                        )
                    shutil.copy2(binary, repo_elf)
                    copied_elf = True
                initrd = make_initrd(self, binary.name)
                with tempfile.TemporaryDirectory(prefix=f"nanvix_{name}_") as tmpdir:
                    tmpdir_path = Path(tmpdir)
                    ramfs_dir = tmpdir_path / "ramfs"
                    ramfs_dir.mkdir()
                    (ramfs_dir / "tmp").mkdir(exist_ok=True)
                    ramfs_img = tmpdir_path / f"rootfs_{name}.img"

                    run(
                        str(mkramfs),
                        "-o",
                        str(ramfs_img),
                        str(ramfs_dir),
                    )

                    run(
                        str(nanvixd),
                        "-bin-dir",
                        str(sysroot_path / "bin"),
                        "-ramfs",
                        str(ramfs_img),
                        "--",
                        str(initrd),
                        timeout=120,
                    )
                print(f"OK   {name}")
            except SystemExit:
                print(f"FAIL {name}")
                failed.append(name)
            finally:
                if initrd is not None and initrd.exists():
                    initrd.unlink()
                if copied_elf and repo_elf.exists():
                    repo_elf.unlink()

        if failed:
            msg = " ".join(failed)
            err_msg = f"{len(failed)} test(s) failed: {msg}"
            raise RuntimeError(err_msg)
        print(f"\t\t*** All {len(test_binaries)} tests PASSED ***")

    def release(self) -> None:
        """Package the libffi release tarball and verify it."""
        run(*self._make_args("package"), cwd=self.repo_root)
        run(*self._make_args("verify-package"), cwd=self.repo_root)

    def clean(self) -> None:
        """Remove build artifacts."""
        run(
            "make",
            "-f",
            "Makefile.nanvix",
            "clean",
            cwd=self.repo_root,
        )


if __name__ == "__main__":
    LibffiBuild.main()
