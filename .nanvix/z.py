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
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

import subprocess
import sys

from nanvix_zutil import CFG_SYSROOT, CFG_TOOLCHAIN, EXIT_MISSING_DEP, ZScript, log

# Makefile variable names (build-system-specific).
_MAKE_VAR_CONFIG = "CONFIG_NANVIX"
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
        toolchain = self.config.get(CFG_TOOLCHAIN, "/opt/nanvix")
        sysroot_p = self.translate_path(Path(sysroot))
        toolchain_p = self.translate_path(Path(toolchain))

        args = [
            "make", "-f", "Makefile.nanvix",
            f"{_MAKE_VAR_CONFIG}=y",
            f"{_MAKE_VAR_HOME}={sysroot_p}",
            f"{_MAKE_VAR_TOOLCHAIN}={toolchain_p}",
        ]

        args.extend([
            f"{_MAKE_VAR_PLATFORM}={self.config.machine}",
            f"{_MAKE_VAR_PROCESS_MODE}={self.config.deployment_mode}",
            f"{_MAKE_VAR_MEMORY_SIZE}={self.config.memory_size}",
        ])

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
                tf.extractall(tmp)
            extracted = Path(tmp) / f"libffi-{_LIBFFI_VERSION}"
            for item in extracted.iterdir():
                dest = self.repo_root / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
        log.info("configure script ready")

    def setup(self) -> None:
        """Download the Nanvix sysroot and prepare autotools sources."""
        super().setup()
        self._ensure_configure()

    def build(self) -> None:
        """Cross-compile libffi.a for Nanvix."""
        self.run(*self._make_args("all"), cwd=self.repo_root)

    def test(self) -> None:
        """Run the test suite.

        On non-Windows, delegates to the Makefile (smoke + integration + functional).
        On Windows, runs test binaries from build/ via nanvixd.exe natively,
        following the same pattern as posix-tests and cpython.
        """
        if IS_WINDOWS:
            self._run_tests_windows()
            return
        targets = self.targets if self.targets else ["test"]
        self.run(*self._make_args(*targets), cwd=self.repo_root)

    def _run_tests_windows(self) -> None:
        """Run tests natively on Windows using nanvixd.exe.

        Only standalone mode is tested on Windows; multi-process and
        single-process require linuxd, which is Linux-only.  Standalone
        test binaries are discovered in the repository root, where the
        Makefile emits the ELF outputs, then fall back to ``build/``.
        """
        if self.config.deployment_mode != "standalone":
            print(f"Skipping tests on Windows for mode '{self.config.deployment_mode}' (requires linuxd).")
            return

        sysroot = self.config.get(CFG_SYSROOT, "")
        if not sysroot:
            log.fatal(f"{CFG_SYSROOT} is not set.", code=EXIT_MISSING_DEP, hint="Run `./z setup` first.")
        sysroot_path = Path(sysroot)
        nanvixd = sysroot_path / "bin" / "nanvixd.exe"
        mkramfs = sysroot_path / "bin" / "mkramfs.exe"
        if not nanvixd.is_file():
            log.fatal("nanvixd.exe not found.", code=EXIT_MISSING_DEP, hint="Run `./z setup` first.")
        if not mkramfs.is_file():
            log.fatal("mkramfs.exe not found.", code=EXIT_MISSING_DEP, hint="Run `./z setup` first.")

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
                f"No allowlisted test binaries found. Expected: {expected}.",
                code=EXIT_MISSING_DEP,
                hint="Build the test binaries first (run the Linux CI build) and then rerun `./z test`.",
            )

        failed = []
        for binary in test_binaries:
            name = binary.stem
            print(f"RUN  {name}...")
            with tempfile.TemporaryDirectory(prefix=f"nanvix_{name}_") as tmpdir:
                tmpdir_path = Path(tmpdir)
                ramfs_dir = tmpdir_path / "ramfs"
                ramfs_dir.mkdir()
                (ramfs_dir / "tmp").mkdir(exist_ok=True)
                shutil.copy2(binary, ramfs_dir / binary.name)
                # Write ramfs image alongside the ramfs source dir to avoid
                # self-inclusion while keeping artifacts scoped to this temp dir.
                ramfs_img = tmpdir_path / f"rootfs_{name}.img"
                try:
                    subprocess.run(
                        [str(mkramfs.resolve()), "-o", str(ramfs_img), str(ramfs_dir)],
                        check=True, timeout=60,
                    )
                except subprocess.CalledProcessError as e:
                    print(f"FAIL {name} (mkramfs exit code {e.returncode})")
                    failed.append(name)
                    continue
                except subprocess.TimeoutExpired:
                    print(f"FAIL {name} (mkramfs timeout)")
                    failed.append(name)
                    continue
                try:
                    result = subprocess.run(
                        [str(nanvixd.resolve()), "-bin-dir", str((sysroot_path / "bin").resolve()),
                         "-ramfs", str(ramfs_img), "--", f"./{binary.name}"],
                        stdin=subprocess.DEVNULL, timeout=120,
                    )
                    if result.returncode != 0:
                        print(f"FAIL {name} (exit code {result.returncode})")
                        failed.append(name)
                    else:
                        print(f"OK   {name}")
                except subprocess.TimeoutExpired:
                    print(f"FAIL {name} (timeout)")
                    failed.append(name)

        if failed:
            msg = " ".join(failed)
            raise RuntimeError(f"{len(failed)} test(s) failed: {msg}")
        print(f"\t\t*** All {len(test_binaries)} tests PASSED ***")

    def release(self) -> None:
        """Package the libffi release tarball and verify it."""
        self.run(*self._make_args("package"), cwd=self.repo_root)
        self.run(*self._make_args("verify-package"), cwd=self.repo_root)

    def clean(self) -> None:
        """Remove build artifacts."""
        self.run(
            "make", "-f", "Makefile.nanvix", "clean",
            cwd=self.repo_root,
        )


if __name__ == "__main__":
    LibffiBuild.main()
