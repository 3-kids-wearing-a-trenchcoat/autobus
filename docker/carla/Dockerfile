# CARLA 0.9.16 on a base new enough to drive a 2025 AMD GPU.
#
# The shipped image is Ubuntu 20.04 with Mesa 21.2, which predates both of
# our GPUs. It cannot drive either one: it reports
#
#     amdgpu: unknown (family_id, chip_external_rev): (152, 81)
#
# and the server exits within 30 s. The two workarounds we had been using
# were each host-specific -- mounting the host's entire /usr/lib over the
# container's (Arch), or falling back to software Vulkan (Fedora). The
# first replaces the container's whole userspace, so CARLA's stability
# became a function of the host's package versions; the second put
# rendering on the CPU, where a map load outlasts UE4's 60 s render-thread
# watchdog and kills the server.
#
# Rebasing solves both. Ubuntu 25.04 ships Mesa 25.0, which supports RDNA
# 3.5 (gfx1150, the Radeon 890M in a Ryzen AI 9 HX 370) and RDNA 4
# (gfx1201, the RX 9070) out of the box, from the container's OWN packages.
# The host then supplies nothing but the kernel and /dev/dri, so the same
# image runs on Arch and on Fedora.
#
# Running an Ubuntu 20.04 binary on a newer glibc is the safe direction:
# glibc and libstdc++ keep backward compatibility, and the CarlaUE4 binary
# needs only libc, libdl, libgcc_s, libm, libpthread and librt -- everything
# else it ships or dlopens.
FROM carlasim/carla:0.9.16 AS carla

FROM ubuntu:25.04

# mesa-vulkan-drivers is RADV, the whole point of this image. The rest is
# what UE4 dlopens at runtime; the binary itself links almost nothing.
RUN apt-get update && apt-get install -y --no-install-recommends \
        mesa-vulkan-drivers \
        libvulkan1 \
        libx11-6 \
        libxext6 \
        libxrandr2 \
        libxcursor1 \
        libxinerama1 \
        libxi6 \
        libxss1 \
        libxxf86vm1 \
        libxcb-keysyms1 \
        libsm6 \
        libice6 \
        libasound2t64 \
        libomp5 \
        xdg-user-dirs \
    && rm -rf /var/lib/apt/lists/*

COPY --from=carla /workspace /workspace

# UE4 writes its logs and config under HOME, so uid 1000 needs one it owns.
RUN mkdir -p /home/carla && chown 1000:1000 /home/carla
ENV HOME=/home/carla

WORKDIR /workspace
USER 1000:1000
CMD ["/bin/bash", "CarlaUE4.sh"]
