#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

mkdir -p third_party
mkdir -p third_party/pseudo_label_kd_acr/checkpoints/btc
mkdir -p third_party/pseudo_label_kd_acr/checkpoints/SL
mkdir -p third_party/pseudo_label_kd_acr/config

cat <<'MSG'
Prepared third_party/ layout:

  third_party/
  third_party/pseudo_label_kd_acr/

Next steps:

  git clone https://github.com/OpenMOSS/MOSS-Music.git third_party/MOSS-Music
  git clone https://github.com/ASLP-lab/SongFormer.git third_party/SongFormer

For ACR, place the pseudo-labeling/KD BTC runtime in:

  third_party/pseudo_label_kd_acr/

Required ACR files:

  btc_chord_recognition.py
  config/btc_config.yaml
  checkpoints/btc/btc_combined_best.pth
  checkpoints/SL/btc_model_large_voca.pt
MSG
