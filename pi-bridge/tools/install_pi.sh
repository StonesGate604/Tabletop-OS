#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo: sudo bash tools/install_pi.sh /path/to/rpi-rgb-led-matrix" >&2
  exit 1
fi

if [[ $# -ne 1 ]]; then
  echo "Usage: sudo bash tools/install_pi.sh /path/to/rpi-rgb-led-matrix" >&2
  exit 1
fi

rgb_matrix_root=$(realpath "$1")
project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
build_dir="${project_root}/build-pi"

if [[ ! -f "${rgb_matrix_root}/include/led-matrix.h" ||
      ! -f "${rgb_matrix_root}/lib/librgbmatrix.a" ]]; then
  echo "The hzeller checkout is missing its header or built library: ${rgb_matrix_root}" >&2
  exit 1
fi

for command_name in cmake g++ getent groupadd id install nproc realpath \
  useradd usermod systemctl; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Required command is missing: ${command_name}" >&2
    exit 1
  fi
done

if ! getent group tabletop-os >/dev/null; then
  groupadd --system tabletop-os
fi
if ! id -u tabletop-os >/dev/null 2>&1; then
  useradd --system --gid tabletop-os --no-create-home \
    --shell /usr/sbin/nologin tabletop-os
fi
usermod -aG dialout tabletop-os
if getent group gpio >/dev/null; then
  usermod -aG gpio tabletop-os
fi

cmake -S "${project_root}" -B "${build_dir}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DTABLETOP_OS_ENABLE_HUB75=ON \
  -DRGB_MATRIX_ROOT="${rgb_matrix_root}"
cmake --build "${build_dir}" --parallel "$(nproc)"

install -m 0755 "${build_dir}/tabletop-os-bridge" /usr/local/bin/tabletop-os-bridge
install -d -m 0755 /etc/tabletop-os
if [[ ! -e /etc/tabletop-os/tabletop-os.json ]]; then
  install -m 0644 "${project_root}/config/tabletop-os.json" /etc/tabletop-os/tabletop-os.json
else
  echo "Keeping existing /etc/tabletop-os/tabletop-os.json"
fi
install -m 0644 "${project_root}/systemd/tabletop-os-bridge.service" \
  /etc/systemd/system/tabletop-os-bridge.service
systemctl daemon-reload

echo "Installed. Set the stable Arduino path and panel mapping in:"
echo "  /etc/tabletop-os/tabletop-os.json"
echo "Then start with:"
echo "  sudo systemctl enable --now tabletop-os-bridge"
