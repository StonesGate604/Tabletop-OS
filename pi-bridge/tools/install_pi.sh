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

if ! getent group tessera >/dev/null; then
  groupadd --system tessera
fi
if ! id -u tessera >/dev/null 2>&1; then
  useradd --system --gid tessera --no-create-home \
    --shell /usr/sbin/nologin tessera
fi
usermod -aG dialout tessera
if getent group gpio >/dev/null; then
  usermod -aG gpio tessera
fi

cmake -S "${project_root}" -B "${build_dir}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DTESSERA_ENABLE_HUB75=ON \
  -DRGB_MATRIX_ROOT="${rgb_matrix_root}"
cmake --build "${build_dir}" --parallel "$(nproc)"

install -m 0755 "${build_dir}/tessera-bridge" /usr/local/bin/tessera-bridge
install -d -m 0755 /etc/tessera
if [[ ! -e /etc/tessera/tessera.json ]]; then
  install -m 0644 "${project_root}/config/tessera.json" /etc/tessera/tessera.json
else
  echo "Keeping existing /etc/tessera/tessera.json"
fi
install -m 0644 "${project_root}/systemd/tessera-bridge.service" \
  /etc/systemd/system/tessera-bridge.service
systemctl daemon-reload

echo "Installed. Set the stable Arduino path and panel mapping in:"
echo "  /etc/tessera/tessera.json"
echo "Then start with:"
echo "  sudo systemctl enable --now tessera-bridge"
