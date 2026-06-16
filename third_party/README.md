# Third-Party Runtime Layout

This directory is intentionally kept lightweight. Do not commit upstream repositories, model checkpoints, caches, or generated outputs here.

Prepare the local layout with:

```bash
bash scripts/prepare_third_party.sh
```

Expected local structure:

```text
third_party/
  MOSS-Music/              # optional recommended local LLM runtime
  SongFormer/              # local SongFormer repository and checkpoints
  pseudo_label_kd_acr/     # ACR method runtime and checkpoints
```

The ACR runtime directory should contain:

```text
third_party/pseudo_label_kd_acr/
  btc_chord_recognition.py
  config/
    btc_config.yaml
  checkpoints/
    btc/
      btc_combined_best.pth
    SL/
      btc_model_large_voca.pt
```

The top-level `.gitignore` ignores everything under `third_party/` except this README.
