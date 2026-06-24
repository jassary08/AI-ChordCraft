#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# ---------------------------------------------------------------------------
# Prepares third_party/ so the demo is runnable right after this script:
#   - clones the public upstream repos (MOSS-Music, SongFormer) if missing
#   - fetches SongFormer pretrained checkpoints (best-effort)
#   - clones ChordMiniApp and syncs its ChordMini runtime into
#     third_party/acr_model (btc_chord_recognition.py + config + modules)
#
# Re-running is safe: existing clones are skipped (or updated with --update),
# and directory/symlink creation is idempotent.
#
# Note on weights: ChordMiniApp pins an older ChordMini submodule commit without
# BTC checkpoints. The current ChordMini main branch contains the public BTC
# checkpoint files, so this script can export them from that remote branch.
# ---------------------------------------------------------------------------

CHORDMINIAPP_URL="https://github.com/ptnghia-j/ChordMiniApp.git"
CHORDMINI_URL="https://github.com/ptnghia-j/ChordMini.git"
CHORDMINI_CHECKPOINT_REF="${CHORDCRAFT_CHORDMINI_CHECKPOINT_REF:-main}"
ACR_SUBPATH="python_backend/models/ChordMini"   # submodule inside ChordMiniApp
UPDATE=0
SKIP_SONGFORMER_CKPT=0
SKIP_ACR_CKPT=0
for arg in "$@"; do
  case "$arg" in
    --update) UPDATE=1 ;;
    --skip-songformer-ckpt) SKIP_SONGFORMER_CKPT=1 ;;
    --skip-acr-ckpt) SKIP_ACR_CKPT=1 ;;
    -h|--help)
      cat <<'USAGE'
Usage: scripts/prepare_third_party.sh [options]

  --update                 git pull existing clones instead of skipping them
  --skip-songformer-ckpt   do not run SongFormer's checkpoint fetch step
  --skip-acr-ckpt          do not run Git LFS / URL download for ACR weights
  -h, --help               show this help

Environment:
  CHORDCRAFT_CHORDMINIAPP_DIR   path to an existing ChordMiniApp checkout to
                                reuse instead of cloning a fresh copy (e.g. a
                                copy already living under another repo).
  CHORDCRAFT_ACR_PL_CHECKPOINT_URL
                                direct URL for the BTC PL checkpoint
  CHORDCRAFT_ACR_SL_CHECKPOINT_URL
                                direct URL for the BTC teacher checkpoint
  CHORDCRAFT_CHORDMINI_CHECKPOINT_REF
                                ChordMini ref to export public BTC checkpoints
                                from when direct URLs are not set (default: main)
USAGE
      exit 0 ;;
    *) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

if ! command -v git >/dev/null 2>&1; then
  echo "error: git is required but not found on PATH." >&2
  exit 1
fi

mkdir -p third_party

# clone_repo <url> <dest> [extra git clone args...]
clone_repo() {
  local url="$1" dest="$2"; shift 2
  if [ -d "$dest/.git" ]; then
    if [ "$UPDATE" -eq 1 ]; then
      echo ">> updating $dest"
      git -C "$dest" pull --ff-only || echo "   (pull skipped/failed; leaving as-is)"
    else
      echo ">> $dest already present, skipping (use --update to refresh)"
    fi
  elif [ -e "$dest" ]; then
    echo ">> $dest exists but is not a git clone, leaving untouched"
  else
    echo ">> cloning $url -> $dest"
    git clone "$@" "$url" "$dest"
  fi
}

clone_repo "https://github.com/OpenMOSS/MOSS-Music.git" "third_party/MOSS-Music" --depth 1
clone_repo "https://github.com/ASLP-lab/SongFormer.git" "third_party/SongFormer" --depth 1

download_file() {
  local url="$1" target="$2"
  if [ -z "$url" ]; then
    return 0
  fi
  if [ -s "$target" ] && ! is_lfs_pointer "$target"; then
    echo ">> $target already exists, skipping download"
    return 0
  fi
  mkdir -p "$(dirname "$target")"
  local tmp="${target}.tmp"
  echo ">> downloading $url -> $target"
  if command -v curl >/dev/null 2>&1; then
    curl -L --fail --retry 3 -o "$tmp" "$url" || {
      rm -f "$tmp"
      return 1
    }
  elif command -v wget >/dev/null 2>&1; then
    wget -O "$tmp" "$url" || {
      rm -f "$tmp"
      return 1
    }
  else
    echo "   (curl or wget is required for direct checkpoint downloads)" >&2
    return 1
  fi
  mv "$tmp" "$target"
}

is_lfs_pointer() {
  local path="$1"
  [ -f "$path" ] && head -c 80 "$path" | grep -q "version https://git-lfs.github.com/spec"
}

is_git_worktree() {
  local path="$1"
  git -C "$path" rev-parse --is-inside-work-tree >/dev/null 2>&1
}

has_git_lfs() {
  git lfs version >/dev/null 2>&1
}

install_checkpoint_alias() {
  local source="$1" target="$2"
  if [ ! -f "$source" ] || is_lfs_pointer "$source"; then
    return 0
  fi
  if [ -s "$target" ] && ! is_lfs_pointer "$target"; then
    return 0
  fi
  mkdir -p "$(dirname "$target")"
  echo ">> placing ACR checkpoint $target"
  ln "$source" "$target" 2>/dev/null || cp "$source" "$target"
}

export_checkpoint_from_git() {
  local repo="$1" ref="$2" source_path="$3" target="$4"
  if [ -s "$target" ] && ! is_lfs_pointer "$target"; then
    return 0
  fi
  if ! is_git_worktree "$repo"; then
    return 1
  fi

  echo ">> exporting $source_path from ChordMini $ref -> $target"
  mkdir -p "$(dirname "$target")"
  local tmp="${target}.tmp"
  if git -C "$repo" cat-file -e "$ref:$source_path" 2>/dev/null \
    && git -C "$repo" show "$ref:$source_path" > "$tmp"; then
    if [ -s "$tmp" ] && ! is_lfs_pointer "$tmp"; then
      mv "$tmp" "$target"
      return 0
    fi
  fi
  rm -f "$tmp"
  return 1
}

fetch_acr_checkpoints() {
  local source_root="$1" acr_root="$2"
  local pl_target="$acr_root/checkpoints/btc/btc_combined_best.pth"
  local sl_target="$acr_root/checkpoints/SL/btc_model_large_voca.pt"

  mkdir -p "$acr_root/checkpoints/btc" "$acr_root/checkpoints/SL"

  if [ "$SKIP_ACR_CKPT" -eq 0 ]; then
    if [ -n "${CHORDCRAFT_ACR_PL_CHECKPOINT_URL:-}" ]; then
      download_file "$CHORDCRAFT_ACR_PL_CHECKPOINT_URL" "$pl_target" \
        || echo "   (PL checkpoint direct download failed; continuing)"
    fi
    if [ -n "${CHORDCRAFT_ACR_SL_CHECKPOINT_URL:-}" ]; then
      download_file "$CHORDCRAFT_ACR_SL_CHECKPOINT_URL" "$sl_target" \
        || echo "   (SL checkpoint direct download failed; continuing)"
    fi
  fi

  # ChordMini's upstream layout may keep these at checkpoints/*. Normalize the
  # paths AI-ChordCraft checks without moving the upstream files.
  install_checkpoint_alias "$source_root/checkpoints/btc_model_best.pth" "$pl_target"
  install_checkpoint_alias "$source_root/checkpoints/btc_combined_best.pth" "$pl_target"
  install_checkpoint_alias "$source_root/checkpoints/btc_model_large_voca.pt" "$sl_target"

  if [ "$SKIP_ACR_CKPT" -eq 0 ]; then
    if ! { [ -s "$pl_target" ] && ! is_lfs_pointer "$pl_target"; }; then
      export_checkpoint_from_git "third_party/ChordMini" "$CHORDMINI_CHECKPOINT_REF" \
        "checkpoints/btc_model_best.pth" "$pl_target" \
        || echo "   (PL checkpoint export from ChordMini failed; continuing)"
    fi
    if ! { [ -s "$sl_target" ] && ! is_lfs_pointer "$sl_target"; }; then
      export_checkpoint_from_git "third_party/ChordMini" "$CHORDMINI_CHECKPOINT_REF" \
        "checkpoints/btc_model_large_voca.pt" "$sl_target" \
        || echo "   (SL checkpoint export from ChordMini failed; continuing)"
    fi
  fi
}

sync_acr_runtime() {
  local source_root="$1" acr_root="$2"
  mkdir -p "$acr_root/checkpoints/btc" "$acr_root/checkpoints/SL"

  echo ">> syncing ACR runtime code -> $acr_root"
  shopt -s dotglob nullglob
  local item base
  for item in "$source_root"/*; do
    base="$(basename "$item")"
    case "$base" in
      .git|checkpoints)
        continue
        ;;
    esac
    rm -rf "$acr_root/$base"
    cp -a "$item" "$acr_root/"
  done
  shopt -u dotglob nullglob
}

patch_acr_runtime() {
  local acr_root="$1"
  local target="$acr_root/btc_chord_recognition.py"
  if [ ! -f "$target" ] || grep -q "NUMBA_DISABLE_CACHE" "$target"; then
    return 0
  fi
  echo ">> patching ACR runtime numba cache settings"
  python - "$target" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
needle = "from scipy import interpolate\n"
patch = (
    "from scipy import interpolate\n\n"
    "os.environ.setdefault(\"NUMBA_CACHE_DIR\", \"/tmp/chordcraft_numba_cache\")\n"
    "os.environ.setdefault(\"NUMBA_DISABLE_CACHE\", \"1\")\n"
)
if "NUMBA_DISABLE_CACHE" not in text and needle in text:
    path.write_text(text.replace(needle, patch, 1), encoding="utf-8")
PY
}

# SongFormer pretrained checkpoints (best-effort; needs network + deps).
if [ "$SKIP_SONGFORMER_CKPT" -eq 0 ] && [ -f "third_party/SongFormer/utils/fetch_pretrained.py" ]; then
  echo ">> fetching SongFormer pretrained checkpoints"
  ( cd third_party/SongFormer && python utils/fetch_pretrained.py ) \
    || echo "   (SongFormer checkpoint fetch failed; run it manually later)"
fi

# --- ACR runtime via ChordMiniApp -----------------------------------------
# The ACR model ChordCraft uses is ChordMiniApp's pinned ChordMini submodule.
# We only need that one submodule, not ChordMiniApp's heavy SongFormer/MuQ ones.
if [ -n "${CHORDCRAFT_CHORDMINIAPP_DIR:-}" ]; then
  CHORDMINIAPP_DIR="$CHORDCRAFT_CHORDMINIAPP_DIR"
  echo ">> reusing existing ChordMiniApp at $CHORDMINIAPP_DIR"
else
  CHORDMINIAPP_DIR="third_party/ChordMiniApp"
  clone_repo "$CHORDMINIAPP_URL" "$CHORDMINIAPP_DIR" --depth 1
fi

clone_repo "$CHORDMINI_URL" "third_party/ChordMini" --depth 1 --branch "$CHORDMINI_CHECKPOINT_REF"

ACR_RUNTIME="$CHORDMINIAPP_DIR/$ACR_SUBPATH"
if [ -d "$CHORDMINIAPP_DIR/.git" ] && [ ! -f "$ACR_RUNTIME/btc_chord_recognition.py" ]; then
  echo ">> initializing ChordMini submodule ($ACR_SUBPATH)"
  git -C "$CHORDMINIAPP_DIR" submodule update --init --depth 1 "$ACR_SUBPATH" \
    || echo "   (submodule init failed; check network/credentials)"
fi

# Sync third_party/acr_model from the ChordMini runtime, while keeping
# third_party/acr_model/checkpoints as the stable location for downloaded weights.
if [ -f "$ACR_RUNTIME/btc_chord_recognition.py" ]; then
  ACR_TARGET="$(cd "$ACR_RUNTIME" && pwd)"
  if [ -L third_party/acr_model ]; then
    echo ">> replacing old third_party/acr_model symlink with a real directory"
    rm third_party/acr_model
  fi
  sync_acr_runtime "$ACR_TARGET" "third_party/acr_model"
  patch_acr_runtime "third_party/acr_model"
  fetch_acr_checkpoints "$ACR_TARGET" "third_party/acr_model"
fi

# --- report ----------------------------------------------------------------
acr_dir="third_party/acr_model"
have_code=0; have_sl=0; have_pl=0
[ -e "$acr_dir/btc_chord_recognition.py" ] && [ -e "$acr_dir/config/btc_config.yaml" ] && have_code=1
[ -e "$acr_dir/checkpoints/SL/btc_model_large_voca.pt" ] \
  && ! is_lfs_pointer "$acr_dir/checkpoints/SL/btc_model_large_voca.pt" \
  && have_sl=1
[ -e "$acr_dir/checkpoints/btc/btc_combined_best.pth" ] \
  && ! is_lfs_pointer "$acr_dir/checkpoints/btc/btc_combined_best.pth" \
  && have_pl=1

cat <<MSG

Prepared third_party/ layout:

  third_party/MOSS-Music/      (optional local LLM runtime)
  third_party/SongFormer/      (structure segmentation)
  third_party/ChordMiniApp/    (source of the ACR runtime)
  third_party/acr_model/       (synced ACR runtime + checkpoints)

ACR runtime status:
MSG

[ "$have_code" -eq 1 ] && echo "  [ok]      runtime code (btc_chord_recognition.py + config)" \
                       || echo "  [missing] runtime code -- ChordMini submodule not initialized"
[ "$have_sl" -eq 1 ] && echo "  [ok]      SL weights  (checkpoints/SL/btc_model_large_voca.pt)" \
                     || echo "  [missing] SL weights  (checkpoints/SL/btc_model_large_voca.pt)"
[ "$have_pl" -eq 1 ] && echo "  [ok]      PL weights  (checkpoints/btc/btc_combined_best.pth)" \
                     || echo "  [missing] PL weights  (checkpoints/btc/btc_combined_best.pth)"

if [ "$have_code" -eq 1 ] && [ "$have_sl" -eq 1 ] && [ "$have_pl" -eq 1 ]; then
  echo ""
  echo "ACR runtime is fully populated. The demo is ready to run."
elif [ "$have_code" -eq 1 ] && { [ "$have_sl" -eq 1 ] || [ "$have_pl" -eq 1 ]; }; then
  cat <<'MSG'

At least one BTC variant is runnable. For any missing ACR weights, set
CHORDCRAFT_ACR_PL_CHECKPOINT_URL / CHORDCRAFT_ACR_SL_CHECKPOINT_URL to real
checkpoint URLs, or place the files directly under third_party/acr_model/checkpoints/.
MOSS-Music / SongFormer checkpoints may also need downloading from their
upstream release pages.
MSG
else
  cat <<MSG

The BTC model weights are still missing. ChordMiniApp references these files but
does not publish them as cloneable Git objects in the checked upstream tree.

Provide real checkpoint URLs:

  CHORDCRAFT_ACR_PL_CHECKPOINT_URL=...
  CHORDCRAFT_ACR_SL_CHECKPOINT_URL=...

or place the files directly under:

  third_party/acr_model/checkpoints/btc/btc_combined_best.pth
  third_party/acr_model/checkpoints/SL/btc_model_large_voca.pt

MOSS-Music / SongFormer checkpoints may also need downloading from their
upstream release pages.
MSG
fi
