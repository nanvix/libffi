# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.
#
# toolchain-libffi — Nanvix LLVM/clang toolchain + autotools for libffi
# cross-compilation. Published as ghcr.io/nanvix/toolchain-libffi.
#
# The base image ships the i686-unknown-nanvix clang cross-compiler and the
# bundled guest runtime (crt0.o, libc.a, libm.a, user.ld) but only a partial
# autotools stack (it lacks the complete libtool macros, e.g.
# LT_SYS_SYMBOL_USCORE, as well as texinfo). libffi's Makefile.nanvix runs
# `autoreconf -i` from the configure target, so a full autoconf / automake /
# libtool / m4 / texinfo stack must be available inside the container.

FROM ghcr.io/nanvix/llvm-project:ca7933e47d3a

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        autoconf \
        automake \
        libtool \
        libtool-bin \
        libltdl-dev \
        m4 \
        texinfo \
    && rm -rf /var/lib/apt/lists/*
