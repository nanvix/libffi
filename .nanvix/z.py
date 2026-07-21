# pyright: basic
# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Nanvix build script for libffi.

Usage:
    ./z setup     # Download Nanvix sysroot
    ./z build     # Cross-compile libffi.a
    ./z test      # Run functional test suite
    ./z release   # Package release tarball
    ./z clean     # Remove build artifacts
"""

import shutil
import sys
import tempfile
from pathlib import Path

from nanvix_zutil import (
    CFG_SYSROOT,
    EXIT_MISSING_DEP,
    TOOLCHAIN_CONTAINER_PATH,
    DockerConfig,
    ZScript,
    log,
    make_initrd,
    run,
)
from nanvix_zutil.paths import (
    dev_out,
    dist_dir,
    nanvix_root,
    out_dir,
    repo_root,
    test_out,
)

# Artifacts produced inside the Docker container that must be copied
# back to the host workspace on Windows tar-copy mode (where the build
# runs in container-local /tmp/build instead of the mounted workspace).
_OUTPUT_FILES = [
    # Makefile leaves ffi_test.elf at the repo root; the standalone test
    # loads it from there. The staged copy under test_out() is for the
    # release tarball.
    "ffi_test.elf",
]


# Makefile variable names (build-system-specific).
_MAKE_VAR_HOME = "NANVIX_HOME"
_MAKE_VAR_TOOLCHAIN = "NANVIX_TOOLCHAIN"
_MAKE_VAR_PLATFORM = "PLATFORM"
_MAKE_VAR_PROCESS_MODE = "PROCESS_MODE"
_MAKE_VAR_MEMORY_SIZE = "MEMORY_SIZE"


IS_WINDOWS = sys.platform == "win32"


class LibffiBuild(ZScript):
    """Build script for nanvix/libffi."""

    # Build-time headers, libraries, startup objects, and linker scripts come
    # from the SDK. The downloaded sysroot is used only to run tests.
    SYSROOT_REQUIRED_FILES = (
        "bin/nanvixd.elf",
        "bin/kernel.elf",
        "bin/mkramfs.elf",
    )
    SYSROOT_REQUIRED_FILES_WINDOWS = (
        "bin/nanvixd.exe",
        "bin/kernel.elf",
        "bin/mkramfs.exe",
    )

    def docker_config(self, image: str) -> DockerConfig:
        """Extend the default Docker config with build output copy-back.

        On Windows the build runs in a container-local directory to avoid
        the slow mounted-workspace I/O penalty.  ``output_files`` tells
        ``nanvix_zutil`` to copy the root test ELF back after the build.
        Staged outputs are written directly to the workspace bind mount.
        """
        cfg = super().docker_config(image)
        cfg.output_files = list(_OUTPUT_FILES)
        return cfg

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

        def translate(p: Path):
            return self.docker.translate_path(p) if self.docker else p

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
                f"NANVIX_ROOT={translate(nanvix_root())}",
                f"OUT_DIR={translate(out_dir())}",
                f"DIST_DIR={translate(dist_dir())}",
                f"LIB_OUT={translate(dev_out() / 'lib')}",
                f"INCLUDE_OUT={translate(dev_out() / 'include')}",
                f"TEST_OUT={translate(test_out())}",
            ]
        )

        args.extend(targets)
        return args

    def setup(self) -> bool:
        """Download the Nanvix sysroot.

        The downloaded sysroot supplies runtime binaries only. Build-time
        headers, libraries, and tools are provided by the SDK image.
        """
        return super().setup()

    def build(self) -> None:
        """Cross-compile libffi.a and ffi_test.elf for Nanvix.

        Both the library and the functional-test binary are produced
        here, where Docker is available. The test step then just runs
        the pre-built binary natively.
        """
        run(*self._make_args("all"), cwd=repo_root(), docker=self.docker)

    def test(self) -> None:
        """Run the standalone functional test suite.

        The functional test is handled in Python so that initrd creation
        is shared across platforms. Only the standalone deployment mode is
        supported.
        """
        if IS_WINDOWS:
            self._run_tests_windows()
            return

        self._run_functional_standalone()

    def _run_functional_standalone(self) -> None:
        """Run the standalone functional test using make_initrd.

        Creates an initrd bundling ffi_test.elf with system daemons via
        make_initrd, and a ramfs providing /tmp for test file output.
        Assumes ``ffi_test.elf`` was produced by ``./z build``.
        """
        ffi_test_elf = repo_root() / "ffi_test.elf"
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
        initrd = make_initrd(ffi_test_elf, test_out())

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

        Only the standalone deployment mode is supported.  Uses
        make_initrd to bundle each test binary with system daemons,
        and a ramfs providing /tmp for any test I/O.
        """
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

        # Discovery order: `test_out()` first (the windows-test artifact
        # overlay location at `.nanvix/out/test/`, populated by the
        # canonical workflow's `download-artifact` step and by
        # `_stage_artifacts_elf_so` in nanvix_scripts for the local sim).
        # Then fall back to `repo_root()` (where the Makefile leaves the
        # link-step output) and `repo_root()/build` (forward-compat).
        test_allowlist = {"ffi_test.elf"}
        test_binaries: list[Path] = []
        for candidate in [test_out(), repo_root(), repo_root() / "build"]:
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
            initrd: Path | None = None
            try:
                initrd = make_initrd(binary, test_out())
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

        if failed:
            msg = " ".join(failed)
            err_msg = f"{len(failed)} test(s) failed: {msg}"
            raise RuntimeError(err_msg)
        print(f"\t\t*** All {len(test_binaries)} tests PASSED ***")

    def clean(self) -> None:
        """Remove build artifacts."""
        run(
            "make",
            "-f",
            "Makefile.nanvix",
            "clean",
            cwd=repo_root(),
        )
        output = out_dir()
        if output.exists():
            shutil.rmtree(output)


if __name__ == "__main__":
    LibffiBuild.main()
