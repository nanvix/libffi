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
    load_manifest,
    package,
)
from nanvix_zutil.paths import (
    dist_dir,
    include_out,
    lib_out,
    nanvix_root,
    out_dir,
    repo_root,
    test_out,
    release_dir,
)

# Artifacts produced inside the Docker container that must be copied
# back to the host workspace on Windows tar-copy mode (where the build
# runs in container-local /tmp/build instead of the mounted workspace).
# Two categories:
#   * legacy repo-root paths needed at runtime (e.g. make_initrd resolves
#     apps via repo_root()/app);
#   * install-staged paths under .nanvix/out/{release,test} required by
#     `./z release` (see _staged_output_files()).
_OUTPUT_FILES = [
    # Kept at repo root because make_initrd() resolves apps via
    # repo_root() / app; the staged copy under test_out() is for the
    # release tarball, not for test execution.
    "ffi_test.elf",
]


def _staged_output_files() -> list[str]:
    """Return install-staged artifact paths (relative to repo_root()) so
    Windows tar-copy mode also copies them back to the host workspace.
    """
    root = repo_root()
    return [
        str((lib_out() / "libffi.a").relative_to(root)),
        str((include_out() / "ffi.h").relative_to(root)),
        str((include_out() / "ffitarget.h").relative_to(root)),
        str((test_out() / "ffi_test.elf").relative_to(root)),
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

    def docker_config(self, image: str) -> DockerConfig:
        """Extend the default Docker config with build output copy-back.

        On Windows the build runs in a container-local directory to avoid
        the slow mounted-workspace I/O penalty.  ``output_files`` tells
        ``nanvix_zutil`` which artifacts to copy back to the host after
        the build so subsequent test / package steps can find them.
        """
        cfg = super().docker_config(image)
        cfg.output_files = list(_OUTPUT_FILES) + _staged_output_files()
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
                f"LIB_OUT={translate(lib_out())}",
                f"INCLUDE_OUT={translate(include_out())}",
                f"TEST_OUT={translate(test_out())}",
            ]
        )

        args.extend(targets)
        return args

    def setup(self) -> bool:
        """Download the Nanvix sysroot.

        The autotools build system (``configure``, ``Makefile.in``, ...)
        is regenerated on demand by ``Makefile.nanvix`` inside the Docker
        toolchain image (see the ``configure`` target there).  No
        host-side autotools install is required.
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
        """Run the test suite.

        In standalone mode the functional test is handled in Python so
        that initrd creation is shared across platforms.
        """
        if IS_WINDOWS:
            self._run_tests_windows()
            return

        if self.config.deployment_mode == "standalone":
            self._run_functional_standalone()
        else:
            targets = self.targets if self.targets else ["test"]
            # Timeout bounds the functional run (Makefile no longer relies on
            # the non-portable `timeout --foreground` binary).
            run(
                *self._make_args(*targets),
                cwd=repo_root(),
                timeout=120,
            )

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
        initrd = make_initrd(self, "ffi_test.elf", test=True)

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
        for candidate in [repo_root(), repo_root() / "build"]:
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
            repo_elf = repo_root() / binary.name
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
                initrd = make_initrd(self, binary.name, test=True)
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
        """Package the release archive named per build configuration.

        The base :meth:`ZScript.release` packages ``release_dir()`` under the
        bare package name, so every matrix configuration emits an
        identically-named archive; in CI these collide and overwrite one
        another, leaving the published release with only generic assets.
        Dependents resolve assets by the pattern
        ``{name}-{machine}-{mode}-{mem}`` (e.g.
        ``{name}-microvm-multi-process-128mb``), so the archive must carry that
        name for dependency installation to succeed.
        """
        manifest = load_manifest()
        name = (
            f"{manifest.name}"
            f"-{self.config.machine}"
            f"-{self.config.deployment_mode}"
            f"-{self.config.memory_size}"
        )
        package([release_dir()], dist_dir(), name)

    def clean(self) -> None:
        """Remove build artifacts."""
        run(
            "make",
            "-f",
            "Makefile.nanvix",
            "clean",
            cwd=repo_root(),
        )


if __name__ == "__main__":
    LibffiBuild.main()
