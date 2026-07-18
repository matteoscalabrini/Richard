#!/usr/bin/env bash
# Richard installer — bash bootstrap.
# Turns a Debian/Ubuntu box into a Richard brain box: guarantees OS deps + a
# standalone Python 3.11, then hands off to `richard setup`, which detects the
# hardware and picks the most capable voice stack the box can run.
set -euo pipefail

RICHARD_HOME="${RICHARD_HOME:-/opt/richard}"
RICHARD_SRC="${RICHARD_SRC:-}"   # optional: local path or git URL for the source
RICHARD_REPO="${RICHARD_REPO:-https://github.com/matteoscalabrini/Richard.git}"
RICHARD_BIN_DIR="${RICHARD_BIN_DIR:-/usr/bin}"
TTS_ROOT="${TTS_ROOT:-/opt/voice}"
RICHARD_GPU="${RICHARD_GPU:-}"
SETUP_ARGS=("$@")

log() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

if [[ -z "$RICHARD_GPU" ]]; then
  for ((i = 0; i < ${#SETUP_ARGS[@]}; i++)); do
    if [[ "${SETUP_ARGS[$i]}" == "--gpu" ]]; then
      ((i + 1 < ${#SETUP_ARGS[@]})) || die "--gpu requires a value"
      RICHARD_GPU="${SETUP_ARGS[$((i + 1))]}"
      break
    fi
  done
fi
RICHARD_GPU="${RICHARD_GPU:-0}"

# 1. OS gate ----------------------------------------------------------------
[[ "$(id -u)" == "0" ]] || die "run the installer as root (sudo ./install.sh)"
command -v apt-get >/dev/null 2>&1 || die "The Richard installer supports Debian/Ubuntu (apt). Detected: $(uname -s)."

# 2. System packages (the deps pip cannot provide) --------------------------
log "Installing system dependencies (apt)…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
PKGS=(build-essential pkg-config git curl ca-certificates ffmpeg libsndfile1 \
      portaudio19-dev espeak-ng python3-dev software-properties-common)
for pkg in "${PKGS[@]}"; do
  if ! dpkg -s "$pkg" >/dev/null 2>&1; then
    apt-get install -y "$pkg" || die "failed to install apt package: $pkg"
  fi
done

# 3. uv + standalone CPython 3.11 ------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  log "Installing uv…"
  curl -fsSL https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
log "Provisioning Python 3.11 via uv…"
uv python install 3.11

# 4. Fetch source -----------------------------------------------------------
mkdir -p "$RICHARD_HOME"
if [ -n "$RICHARD_SRC" ] && [ -d "$RICHARD_SRC" ]; then
  source_path="$(cd "$RICHARD_SRC" && pwd -P)"
  home_path="$(cd "$RICHARD_HOME" && pwd -P)"
  if [[ "$source_path" != "$home_path" ]]; then
    log "Copying source from ${RICHARD_SRC}…"
    cp -a "$RICHARD_SRC/." "$RICHARD_HOME/"
  fi
elif [ -n "$RICHARD_SRC" ]; then
  log "Cloning source from ${RICHARD_SRC}…"
  git clone "$RICHARD_SRC" "$RICHARD_HOME"
elif [ ! -f "$RICHARD_HOME/pyproject.toml" ]; then
  log "Cloning Richard from ${RICHARD_REPO}…"
  git clone "$RICHARD_REPO" "$RICHARD_HOME"
fi
chmod +x "$RICHARD_HOME/install.sh" "$RICHARD_HOME/update.sh"

# 5. venv + install ---------------------------------------------------------
cd "$RICHARD_HOME"
log "Creating venv + installing Richard…"
if [ ! -x .venv/bin/python ]; then
  uv venv --python 3.11 .venv
fi
uv pip install --python .venv/bin/python --upgrade -e ".[voice]"
mkdir -p "$RICHARD_BIN_DIR"
ln -sfn "$RICHARD_HOME/.venv/bin/richard" "$RICHARD_BIN_DIR/richard"

# 6. Hand off to the Python configurator ------------------------------------
log "Handing off to richard setup…"
"$RICHARD_HOME/.venv/bin/richard" setup "${SETUP_ARGS[@]}"

# 7. Download and initialize the in-process realtime models -----------------
log "Preparing realtime voice models…"
CUDA_LIBRARY_PATH="$(
  "$RICHARD_HOME/.venv/bin/python" -m richard.setup.cuda "$TTS_ROOT/venv"
)"
RICHARD_LD_LIBRARY_PATH="$CUDA_LIBRARY_PATH"
if [[ -n "${LD_LIBRARY_PATH:-}" ]]; then
  RICHARD_LD_LIBRARY_PATH="${RICHARD_LD_LIBRARY_PATH:+$RICHARD_LD_LIBRARY_PATH:}$LD_LIBRARY_PATH"
fi
CUDA_VISIBLE_DEVICES="$RICHARD_GPU" \
  LD_LIBRARY_PATH="$RICHARD_LD_LIBRARY_PATH" \
  "$RICHARD_HOME/.venv/bin/python" -m richard.setup.deployment prepare

# 8. Keep realtime voice, relay, and web alive across logout and reboot. ----
cat > /etc/systemd/system/richard.service <<EOF
[Unit]
Description=Richard realtime voice, relay, and web host
Wants=network-online.target
After=network-online.target chatterbox-tts.service

[Service]
Type=simple
WorkingDirectory=$RICHARD_HOME
Environment=CUDA_VISIBLE_DEVICES=$RICHARD_GPU
Environment=LD_LIBRARY_PATH=$RICHARD_LD_LIBRARY_PATH
ExecStart=$RICHARD_HOME/.venv/bin/richard serve
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable richard.service
systemctl restart richard.service

realtime_healthy=0
for ((attempt = 1; attempt <= 24; attempt++)); do
  if "$RICHARD_HOME/.venv/bin/python" -m richard.setup.deployment verify; then
    realtime_healthy=1
    break
  fi
  sleep 5
done
[[ "$realtime_healthy" == "1" ]] || die "Richard realtime health check failed"

commit="unknown"
branch="unknown"
remote="none"
if [ -d "$RICHARD_HOME/.git" ]; then
  commit="$(git -C "$RICHARD_HOME" rev-parse HEAD)"
  branch="$(git -C "$RICHARD_HOME" symbolic-ref --quiet --short HEAD || printf '%s' detached)"
  remote="$(git -C "$RICHARD_HOME" remote get-url origin 2>/dev/null || printf '%s' none)"
fi
cat > "$RICHARD_HOME/.richard-install" <<EOF
installed_commit=$commit
installed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
remote=$remote
branch=$branch
gpu=$RICHARD_GPU
tts_root=$TTS_ROOT
EOF
log "Install complete. Future updates: $RICHARD_HOME/update.sh"
