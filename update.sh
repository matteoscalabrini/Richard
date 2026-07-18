#!/usr/bin/env bash
# Update an installed Richard checkout in place.
#
# The ordinary path updates Richard and its Chatterbox assets. Pass --tts-deps
# to also refresh third-party packages in the external TTS environment.
set -Eeuo pipefail

RICHARD_HOME="${RICHARD_HOME:-/opt/richard}"
TTS_ROOT="${TTS_ROOT:-/opt/voice}"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
RICHARD_BIN_DIR="${RICHARD_BIN_DIR:-/usr/bin}"
UPDATE_REMOTE="${RICHARD_UPDATE_REMOTE:-origin}"
UPDATE_BRANCH="${RICHARD_UPDATE_BRANCH:-}"
LOCK_DIR="${RICHARD_UPDATE_LOCK:-/run/lock/richard-update.lock}"
BACKUP_ROOT="${RICHARD_UPDATE_BACKUP_ROOT:-/var/backups/richard}"
HEALTH_ATTEMPTS="${RICHARD_UPDATE_HEALTH_ATTEMPTS:-24}"
HEALTH_DELAY="${RICHARD_UPDATE_HEALTH_DELAY:-5}"
RICHARD_SERVICE_NAMES="${RICHARD_SERVICE_NAMES:-richard.service richard-serve.service}"
RICHARD_GPU="${RICHARD_GPU:-}"
TTS_MODE="auto"
CHECK_ONLY=0

log() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARN:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: sudo /opt/richard/update.sh [options]

Options:
  --check              Fetch and report whether an update is available; change nothing.
  --branch NAME        Update from this branch (default: the installed checkout's branch).
  --remote NAME        Git remote to fetch (default: origin).
  --tts-deps           Also refresh third-party Chatterbox TTS dependencies.
  --skip-tts           Do not deploy or restart the Chatterbox TTS service.
  -h, --help           Show this help.

Environment overrides:
  RICHARD_HOME, TTS_ROOT, UNIT_DIR, RICHARD_BIN_DIR, RICHARD_GPU,
  RICHARD_UPDATE_REMOTE,
  RICHARD_UPDATE_BRANCH, RICHARD_UPDATE_HEALTH_ATTEMPTS,
  RICHARD_UPDATE_HEALTH_DELAY, RICHARD_SERVICE_NAMES.

Normal updates preserve ~/.richard config/databases and do not advance the external
Chatterbox server repository. --tts-deps upgrades its Python requirements/packages.
EOF
}

while (($#)); do
  case "$1" in
    --check) CHECK_ONLY=1 ;;
    --branch)
      (($# >= 2)) || die "--branch requires a value"
      UPDATE_BRANCH="$2"
      shift
      ;;
    --remote)
      (($# >= 2)) || die "--remote requires a value"
      UPDATE_REMOTE="$2"
      shift
      ;;
    --tts-deps) TTS_MODE="full" ;;
    --skip-tts) TTS_MODE="skip" ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
  shift
done

if [[ "${RICHARD_UPDATE_REEXEC:-0}" != "1" ]]; then
  # A Git update can replace update.sh while Bash is still reading it. Continue from
  # an immutable temporary copy so the updater can safely update itself.
  self_copy="$(mktemp "${TMPDIR:-/tmp}/richard-update.XXXXXX")"
  cp "$0" "$self_copy"
  chmod 700 "$self_copy"
  reexec_args=(--remote "$UPDATE_REMOTE" --branch "$UPDATE_BRANCH")
  [[ "$CHECK_ONLY" == "1" ]] && reexec_args+=(--check)
  [[ "$TTS_MODE" == "full" ]] && reexec_args+=(--tts-deps)
  [[ "$TTS_MODE" == "skip" ]] && reexec_args+=(--skip-tts)
  exec env \
    RICHARD_UPDATE_REEXEC=1 \
    RICHARD_UPDATE_SELF_COPY="$self_copy" \
    RICHARD_HOME="$RICHARD_HOME" \
    TTS_ROOT="$TTS_ROOT" \
    UNIT_DIR="$UNIT_DIR" \
    RICHARD_BIN_DIR="$RICHARD_BIN_DIR" \
    RICHARD_UPDATE_REMOTE="$UPDATE_REMOTE" \
    RICHARD_UPDATE_BRANCH="$UPDATE_BRANCH" \
    RICHARD_UPDATE_LOCK="$LOCK_DIR" \
    RICHARD_UPDATE_BACKUP_ROOT="$BACKUP_ROOT" \
    RICHARD_UPDATE_HEALTH_ATTEMPTS="$HEALTH_ATTEMPTS" \
    RICHARD_UPDATE_HEALTH_DELAY="$HEALTH_DELAY" \
    RICHARD_SERVICE_NAMES="$RICHARD_SERVICE_NAMES" \
    RICHARD_GPU="$RICHARD_GPU" \
    RICHARD_UPDATE_ALLOW_NON_ROOT="${RICHARD_UPDATE_ALLOW_NON_ROOT:-0}" \
    bash "$self_copy" "${reexec_args[@]}"
fi

[[ "$(id -u)" == "0" || "${RICHARD_UPDATE_ALLOW_NON_ROOT:-0}" == "1" ]] || \
  die "run as root (sudo $RICHARD_HOME/update.sh)"

for command in git uv; do
  command -v "$command" >/dev/null 2>&1 || die "required command not found: $command"
done
[[ "$HEALTH_ATTEMPTS" =~ ^[1-9][0-9]*$ ]] || die "health attempts must be a positive integer"
[[ "$HEALTH_DELAY" =~ ^[0-9]+([.][0-9]+)?$ ]] || die "health delay must be a non-negative number"

[[ -d "$RICHARD_HOME/.git" ]] || \
  die "$RICHARD_HOME is not a Git checkout; reinstall once from a Git source before using update.sh"
[[ -f "$RICHARD_HOME/pyproject.toml" ]] || die "not a Richard checkout: $RICHARD_HOME"

mkdir -p "$(dirname "$LOCK_DIR")"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  die "another Richard update is already running ($LOCK_DIR)"
fi

SUCCESS=0
MUTATED=0
TTS_MUTATED=0
TTS_DEPS_MUTATED=0
BACKUP_DIR=""
NEW_VENV=""
OLD_VENV_TARGET=""
ORIGINAL_VENV_KIND="none"
OLD_COMMIT=""
TARGET_COMMIT=""
declare -a ACTIVE_RICHARD_SERVICES=()
declare -a ACTIVE_TTS_SERVICES=()
SERVICE_LIFECYCLE_TOUCHED=0

unit_exists() {
  local unit="$1"
  [[ -f "$UNIT_DIR/$unit" ]] || {
    command -v systemctl >/dev/null 2>&1 && systemctl cat "$unit" >/dev/null 2>&1
  }
}

unit_active() {
  systemctl is-active --quiet "$1"
}

stop_units() {
  local unit
  for unit in "$@"; do
    systemctl stop "$unit"
  done
}

start_units() {
  local unit
  for unit in "$@"; do
    systemctl start "$unit"
  done
}

atomic_venv_link() {
  local target="$1"
  ln -s "$target" "$RICHARD_HOME/.venv.next"
  # The destination is removed/moved immediately before this call, so plain `mv`
  # remains atomic without relying on GNU-only `mv -T`.
  mv -f "$RICHARD_HOME/.venv.next" "$RICHARD_HOME/.venv"
}

restore_file() {
  local backup="$1" destination="$2"
  if [[ -f "$backup" ]]; then
    cp -a "$backup" "$destination"
  else
    rm -f "$destination"
  fi
}

rollback() {
  local rc="$1"
  set +e
  warn "Update failed; rolling back to ${OLD_COMMIT:-the previous deployment}…"

  if [[ "$SERVICE_LIFECYCLE_TOUCHED" == "1" ]] && \
     command -v systemctl >/dev/null 2>&1; then
    ((${#ACTIVE_RICHARD_SERVICES[@]})) && stop_units "${ACTIVE_RICHARD_SERVICES[@]}"
    ((${#ACTIVE_TTS_SERVICES[@]})) && stop_units "${ACTIVE_TTS_SERVICES[@]}"
  fi

  if [[ -n "$OLD_COMMIT" ]]; then
    git -C "$RICHARD_HOME" reset --hard "$OLD_COMMIT" >/dev/null
  fi

  # OLD_VENV_TARGET is assigned as soon as the old environment has either been
  # identified (symlink) or moved aside (directory). Restore it even if the
  # atomic link operation itself was the command that failed.
  if [[ -n "$OLD_VENV_TARGET" ]]; then
    rm -f "$RICHARD_HOME/.venv"
    atomic_venv_link "$OLD_VENV_TARGET"
  fi
  if [[ -n "$NEW_VENV" && -d "$NEW_VENV" ]]; then
    new_real="$(cd "$NEW_VENV" 2>/dev/null && pwd -P)"
    active_real="$(cd "$RICHARD_HOME/.venv" 2>/dev/null && pwd -P)"
    if [[ -n "$new_real" && "$new_real" != "$active_real" ]]; then
      rm -rf "$NEW_VENV"
    fi
  fi

  if [[ "$TTS_MUTATED" == "1" && -n "$BACKUP_DIR" ]]; then
    mkdir -p "$TTS_ROOT/Chatterbox-TTS-Server/reference_audio" "$UNIT_DIR"
    restore_file "$BACKUP_DIR/richard-voice.wav" \
      "$TTS_ROOT/Chatterbox-TTS-Server/reference_audio/richard-voice.wav"
    restore_file "$BACKUP_DIR/chatterbox-tts.service" "$UNIT_DIR/chatterbox-tts.service"
    if [[ "$TTS_DEPS_MUTATED" == "1" && -s "$BACKUP_DIR/tts-freeze.txt" && \
          -x "$TTS_ROOT/venv/bin/pip" ]]; then
      warn "Restoring the previous TTS Python package set…"
      "$TTS_ROOT/venv/bin/pip" install -r "$BACKUP_DIR/tts-freeze.txt" >/dev/null 2>&1 || \
        warn "TTS dependency rollback was incomplete; inspect $BACKUP_DIR/tts-freeze.txt"
    fi
  fi

  if command -v systemctl >/dev/null 2>&1; then
    # Unit files also need to be reloaded when the affected services were
    # inactive and therefore never entered the stop/start lifecycle.
    if [[ "$TTS_MUTATED" == "1" || "$SERVICE_LIFECYCLE_TOUCHED" == "1" ]]; then
      systemctl daemon-reload
    fi
    if [[ "$SERVICE_LIFECYCLE_TOUCHED" == "1" ]]; then
      ((${#ACTIVE_TTS_SERVICES[@]})) && start_units "${ACTIVE_TTS_SERVICES[@]}"
      ((${#ACTIVE_RICHARD_SERVICES[@]})) && start_units "${ACTIVE_RICHARD_SERVICES[@]}"
    fi
  fi
  warn "Rollback complete. Backup details remain in ${BACKUP_DIR:-not-created}."
  return "$rc"
}

cleanup() {
  local rc="$1"
  if [[ "$SUCCESS" != "1" && "$MUTATED" == "1" ]]; then
    rollback "$rc" || true
  fi
  rmdir "$LOCK_DIR" 2>/dev/null || true
  if [[ -n "${RICHARD_UPDATE_SELF_COPY:-}" ]]; then
    rm -f "$RICHARD_UPDATE_SELF_COPY"
  fi
  exit "$rc"
}
trap 'cleanup "$?"' EXIT

cd "$RICHARD_HOME"
git diff --quiet || die "tracked files have local changes; commit or revert them before updating"
git diff --cached --quiet || die "the Git index has staged changes; commit or unstage them first"
git remote get-url "$UPDATE_REMOTE" >/dev/null 2>&1 || die "Git remote not found: $UPDATE_REMOTE"

current_branch="$(git symbolic-ref --quiet --short HEAD || true)"
[[ -n "$current_branch" ]] || die "detached HEAD is not supported; check out the deployment branch first"
if [[ -z "$UPDATE_BRANCH" ]]; then
  UPDATE_BRANCH="$current_branch"
elif [[ "$UPDATE_BRANCH" != "$current_branch" ]]; then
  die "installed branch is $current_branch; check out $UPDATE_BRANCH before changing update branches"
fi

OLD_COMMIT="$(git rev-parse HEAD)"
log "Fetching ${UPDATE_REMOTE}/${UPDATE_BRANCH}…"
git fetch --prune "$UPDATE_REMOTE" "$UPDATE_BRANCH"
TARGET_COMMIT="$(git rev-parse FETCH_HEAD)"

if ! git merge-base --is-ancestor "$OLD_COMMIT" "$TARGET_COMMIT"; then
  die "update is not a fast-forward ($OLD_COMMIT -> $TARGET_COMMIT); refusing to rewrite deployment history"
fi

if [[ "$CHECK_ONLY" == "1" ]]; then
  if [[ "$OLD_COMMIT" == "$TARGET_COMMIT" ]]; then
    log "Richard is up to date at ${OLD_COMMIT:0:12}."
  else
    count="$(git rev-list --count "$OLD_COMMIT..$TARGET_COMMIT")"
    log "Update available: $count commit(s), ${OLD_COMMIT:0:12} -> ${TARGET_COMMIT:0:12}."
    git --no-pager log --oneline --no-decorate "$OLD_COMMIT..$TARGET_COMMIT"
  fi
  SUCCESS=1
  exit 0
fi

TTS_UNIT_PRESENT=0
if unit_exists chatterbox-tts.service; then
  TTS_UNIT_PRESENT=1
fi
TTS_INSTALLED=0
if [[ -x "$TTS_ROOT/venv/bin/pip" && "$TTS_UNIT_PRESENT" == "1" ]]; then
  TTS_INSTALLED=1
fi

TTS_CHANGED=0
tts_changed_files="$(git diff --name-only "$OLD_COMMIT" "$TARGET_COMMIT" -- \
  assets/voice src/richard/setup/services.py src/richard/setup/units.py)"
if [[ "$OLD_COMMIT" != "$TARGET_COMMIT" && -n "$tts_changed_files" ]]; then
  TTS_CHANGED=1
fi
if [[ "$TTS_MODE" == "full" && "$TTS_INSTALLED" != "1" ]]; then
  die "--tts-deps requested, but a complete local Chatterbox deployment was not found"
fi
if [[ "$TTS_UNIT_PRESENT" == "1" && ! -x "$TTS_ROOT/venv/bin/pip" && \
      "$TTS_MODE" != "skip" && "$TTS_CHANGED" == "1" ]]; then
  die "Chatterbox is installed but $TTS_ROOT/venv is incomplete; repair it or use --skip-tts"
fi
TTS_ACTION=0
if [[ "$TTS_INSTALLED" == "1" && "$TTS_MODE" != "skip" ]] && \
   [[ "$TTS_CHANGED" == "1" || "$TTS_MODE" == "full" ]]; then
  TTS_ACTION=1
fi

if [[ "$OLD_COMMIT" == "$TARGET_COMMIT" && "$TTS_ACTION" != "1" ]]; then
  log "Richard is already up to date at ${OLD_COMMIT:0:12}."
  SUCCESS=1
  exit 0
fi

if command -v systemctl >/dev/null 2>&1; then
  if [[ -n "$RICHARD_SERVICE_NAMES" ]]; then
    read -r -a richard_service_candidates <<< "$RICHARD_SERVICE_NAMES"
    for unit in "${richard_service_candidates[@]}"; do
      if unit_exists "$unit" && unit_active "$unit"; then
        ACTIVE_RICHARD_SERVICES+=("$unit")
      fi
    done
  fi
  if [[ "$TTS_ACTION" == "1" ]] && unit_active chatterbox-tts.service; then
    ACTIVE_TTS_SERVICES+=("chatterbox-tts.service")
  fi
elif [[ "$TTS_ACTION" == "1" ]]; then
  die "the Chatterbox service needs systemctl, but it is unavailable"
fi

if [[ -z "$RICHARD_GPU" && -f "$UNIT_DIR/richard.service" ]]; then
  RICHARD_GPU="$(sed -n 's/^Environment=CUDA_VISIBLE_DEVICES=//p' \
    "$UNIT_DIR/richard.service" | head -n1)"
fi
if [[ -z "$RICHARD_GPU" && -f "$UNIT_DIR/chatterbox-tts.service" ]]; then
  RICHARD_GPU="$(sed -n 's/^Environment=CUDA_VISIBLE_DEVICES=//p' \
    "$UNIT_DIR/chatterbox-tts.service" | head -n1)"
fi
RICHARD_GPU="${RICHARD_GPU:-0}"

update_id="$(date -u +%Y%m%dT%H%M%SZ)-${TARGET_COMMIT:0:12}"
BACKUP_DIR="$BACKUP_ROOT/$update_id"
mkdir -p "$BACKUP_DIR"
printf '%s\n' "$OLD_COMMIT" > "$BACKUP_DIR/previous-commit"
printf '%s\n' "$TARGET_COMMIT" > "$BACKUP_DIR/target-commit"

if [[ "$OLD_COMMIT" != "$TARGET_COMMIT" ]]; then
  log "Fast-forwarding Richard to ${TARGET_COMMIT:0:12}…"
  git merge --ff-only "$TARGET_COMMIT"
  MUTATED=1

  mkdir -p "$RICHARD_HOME/.venvs"
  NEW_VENV="$RICHARD_HOME/.venvs/$TARGET_COMMIT"
  if [[ -e "$NEW_VENV" ]]; then
    warn "Removing an inactive environment left by an earlier attempt: $NEW_VENV"
    rm -rf "$NEW_VENV"
  fi
  log "Building the new Richard Python environment…"
  uv venv --python 3.11 "$NEW_VENV"
  uv pip install --python "$NEW_VENV/bin/python" -e "${RICHARD_HOME}[voice]"
  "$NEW_VENV/bin/python" -m compileall -q "$RICHARD_HOME/src"
  "$NEW_VENV/bin/richard" --version

  if [[ -L "$RICHARD_HOME/.venv" ]]; then
    ORIGINAL_VENV_KIND="link"
    OLD_VENV_TARGET="$(readlink "$RICHARD_HOME/.venv")"
  elif [[ -d "$RICHARD_HOME/.venv" ]]; then
    ORIGINAL_VENV_KIND="directory"
    legacy_name="legacy-${OLD_COMMIT:0:12}-$update_id"
    mv "$RICHARD_HOME/.venv" "$RICHARD_HOME/.venvs/$legacy_name"
    OLD_VENV_TARGET=".venvs/$legacy_name"
  fi

  if ((${#ACTIVE_RICHARD_SERVICES[@]})); then
    log "Stopping Richard services for the environment switch…"
    SERVICE_LIFECYCLE_TOUCHED=1
    stop_units "${ACTIVE_RICHARD_SERVICES[@]}"
  fi
  [[ "$ORIGINAL_VENV_KIND" != "none" ]] || die "installed environment not found: $RICHARD_HOME/.venv"
  if [[ "$ORIGINAL_VENV_KIND" == "link" ]]; then
    rm -f "$RICHARD_HOME/.venv"
  fi
  atomic_venv_link ".venvs/$TARGET_COMMIT"

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
fi

if [[ "$TTS_ACTION" == "1" ]]; then
  log "Refreshing Richard-managed GPU TTS service…"
  TTS_MUTATED=1
  MUTATED=1
  [[ -f "$TTS_ROOT/Chatterbox-TTS-Server/reference_audio/richard-voice.wav" ]] && \
    cp -a "$TTS_ROOT/Chatterbox-TTS-Server/reference_audio/richard-voice.wav" \
      "$BACKUP_DIR/richard-voice.wav"
  [[ -f "$UNIT_DIR/chatterbox-tts.service" ]] && \
    cp -a "$UNIT_DIR/chatterbox-tts.service" "$BACKUP_DIR/chatterbox-tts.service"
  if [[ "$TTS_MODE" == "full" ]]; then
    "$TTS_ROOT/venv/bin/pip" freeze > "$BACKUP_DIR/tts-freeze.txt"
  fi

  if ((${#ACTIVE_TTS_SERVICES[@]})); then
    SERVICE_LIFECYCLE_TOUCHED=1
    stop_units "${ACTIVE_TTS_SERVICES[@]}"
  fi

  if [[ -d "$TTS_ROOT/Chatterbox-TTS-Server/reference_audio" ]]; then
    install -m 0644 "$RICHARD_HOME/assets/voice/richard-voice.wav" \
      "$TTS_ROOT/Chatterbox-TTS-Server/reference_audio/richard-voice.wav"
  fi

  if [[ "$TTS_MODE" == "full" ]]; then
    TTS_DEPS_MUTATED=1
    tts_pip="$TTS_ROOT/venv/bin/pip"
    chatterbox_dir="$TTS_ROOT/Chatterbox-TTS-Server"
    [[ -f "$chatterbox_dir/requirements-nvidia.txt" ]] || \
      die "missing Chatterbox requirements: $chatterbox_dir/requirements-nvidia.txt"
    log "Updating third-party TTS dependencies (external repo revision is preserved)…"
    pip_install() { "$tts_pip" install --upgrade "$@"; }
    pip_install -r "$chatterbox_dir/requirements-nvidia.txt"
    pip_install --no-deps \
      "git+https://github.com/devnen/chatterbox-v2.git@master" \
      "s3tokenizer==0.3.0" "onnx==1.16.0"
    pip_install "protobuf==3.20.3"
  fi

  RICHARD_RENDER_UNIT_DIR="$UNIT_DIR" RICHARD_RENDER_TTS_ROOT="$TTS_ROOT" \
    RICHARD_RENDER_GPU="$RICHARD_GPU" "$RICHARD_HOME/.venv/bin/python" - <<'PY'
import os
from pathlib import Path

from richard.setup.units import render_chatterbox_unit

unit_dir = Path(os.environ["RICHARD_RENDER_UNIT_DIR"])
unit_dir.mkdir(parents=True, exist_ok=True)
tts_root = os.environ["RICHARD_RENDER_TTS_ROOT"]
venv = f"{tts_root}/venv"
chatterbox_dir = f"{tts_root}/Chatterbox-TTS-Server"
gpu = os.environ["RICHARD_RENDER_GPU"]
(unit_dir / "chatterbox-tts.service").write_text(
    render_chatterbox_unit(workdir=chatterbox_dir, python=f"{venv}/bin/python", gpu=gpu),
    encoding="utf-8",
)
PY
  systemctl daemon-reload
fi

if ((${#ACTIVE_TTS_SERVICES[@]})); then
  log "Starting Chatterbox TTS service…"
  start_units "${ACTIVE_TTS_SERVICES[@]}"
fi
if ((${#ACTIVE_RICHARD_SERVICES[@]})); then
  log "Starting Richard services…"
  start_units "${ACTIVE_RICHARD_SERVICES[@]}"
fi

wait_http() {
  local name="$1" url="$2" attempt status
  for ((attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt++)); do
    status="$(curl --max-time 5 --silent --show-error --output /dev/null \
      --write-out '%{http_code}' "$url" 2>/dev/null || true)"
    if [[ "$status" =~ ^[234][0-9][0-9]$ ]]; then
      log "$name healthy at $url (HTTP $status)."
      return 0
    fi
    sleep "$HEALTH_DELAY"
  done
  return 1
}

if ((${#ACTIVE_RICHARD_SERVICES[@]})); then
  for unit in "${ACTIVE_RICHARD_SERVICES[@]}"; do
    unit_active "$unit" || die "$unit did not become active"
  done
fi
if ((${#ACTIVE_TTS_SERVICES[@]})); then
  for unit in "${ACTIVE_TTS_SERVICES[@]}"; do
    unit_active "$unit" || die "$unit did not become active"
  done
fi

if [[ "$TTS_ACTION" == "1" ]]; then
  command -v curl >/dev/null 2>&1 || die "curl is required to verify Chatterbox"
  if [[ " ${ACTIVE_TTS_SERVICES[*]} " == *" chatterbox-tts.service "* ]]; then
    wait_http "TTS" "http://127.0.0.1:8004/" || die "Chatterbox TTS health check failed"
  fi
fi

if ((${#ACTIVE_RICHARD_SERVICES[@]})); then
  realtime_healthy=0
  for ((attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt++)); do
    if "$RICHARD_HOME/.venv/bin/python" -m richard.setup.deployment verify; then
      realtime_healthy=1
      break
    fi
    sleep "$HEALTH_DELAY"
  done
  [[ "$realtime_healthy" == "1" ]] || die "Richard realtime health check failed"
fi

cat > "$RICHARD_HOME/.richard-install" <<EOF
installed_commit=$TARGET_COMMIT
updated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
remote=$UPDATE_REMOTE
branch=$UPDATE_BRANCH
gpu=$RICHARD_GPU
tts_root=$TTS_ROOT
EOF

mkdir -p "$RICHARD_BIN_DIR"
ln -sfn "$RICHARD_HOME/.venv/bin/richard" "$RICHARD_BIN_DIR/richard"

SUCCESS=1
log "Richard updated successfully: ${OLD_COMMIT:0:12} -> ${TARGET_COMMIT:0:12}."
if [[ "$TTS_INSTALLED" == "1" && "$TTS_ACTION" != "1" ]]; then
  log "Chatterbox was unchanged and left running. Use --tts-deps for an explicit dependency refresh."
fi
log "Rollback metadata: $BACKUP_DIR"
