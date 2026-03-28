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
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

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

        args = [
            "make", "-f", "Makefile.nanvix",
            f"{_MAKE_VAR_CONFIG}=y",
            f"{_MAKE_VAR_HOME}={sysroot}",
            f"{_MAKE_VAR_TOOLCHAIN}={toolchain}",
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
        """Run the libffi test suite.

        Without targets, runs the full suite (smoke + integration + functional).
        With targets (e.g. ``./z test -- test-smoke test-integration``), passes
        them directly to the Makefile.
        """
        targets = self.targets if self.targets else ["test"]
        self.run(*self._make_args(*targets), cwd=self.repo_root)

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
