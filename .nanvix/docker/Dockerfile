# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.
#
# toolchain-libffi — Nanvix GCC toolchain + autotools for libffi
# cross-compilation. Published as ghcr.io/nanvix/toolchain-libffi.
#
# The base image ships the i686-nanvix cross-compiler but does not include
# autoconf / automake / libtool. libffi's Makefile.nanvix runs `autoreconf -i`
# from the configure target, so those tools must be available inside the
# container.

FROM ghcr.io/nanvix/toolchain-gcc:sha-34a3641

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        autoconf \
        automake \
        libtool \
        libltdl-dev \
        m4 \
        texinfo \
    && rm -rf /var/lib/apt/lists/*
