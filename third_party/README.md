# Third-Party Runtime Layout

This directory is intentionally kept lightweight. Do not commit upstream repositories, model checkpoints, caches, or generated outputs here.

Prepare everything (clone upstream repos + wire the ACR runtime) with:

```bash
bash scripts/prepare_third_party.sh
```

That script clones the public repositories for you, initializes the ChordMini
runtime, and syncs it into `acr_model/`. Re-run with `--update` to refresh
existing clones.

Expected local structure:

```text
third_party/
  MOSS-Music/              # optional recommended local LLM runtime
  SongFormer/              # local SongFormer repository and checkpoints
  ChordMiniApp/            # source of the ACR runtime (cloned by the script)
  acr_model/               # synced ACR runtime used by AI-ChordCraft
```

`acr_model/` is the ACR (automatic chord recognition) runtime. It comes from
[ChordMiniApp](https://github.com/ptnghia-j/ChordMiniApp)'s pinned `ChordMini`
submodule, which provides:

```text
third_party/acr_model/
  btc_chord_recognition.py
  config/
    btc_config.yaml
  modules/                 # BTC model + utils
  checkpoints/
    btc/
      btc_combined_best.pth   # PL variant weights
    SL/
      btc_model_large_voca.pt # SL variant weights
```

The script clones the runtime **code** automatically. It also tries to fetch the
checkpoint **weights** with Git LFS and normalizes them into
`third_party/acr_model/checkpoints/`. If you host the checkpoints elsewhere,
set direct URLs before running the script:

```bash
CHORDCRAFT_ACR_PL_CHECKPOINT_URL=https://.../btc_combined_best.pth \
CHORDCRAFT_ACR_SL_CHECKPOINT_URL=https://.../btc_model_large_voca.pt \
bash scripts/prepare_third_party.sh
```

Having just one variant's weights is enough to run that variant.

If you already have a ChordMiniApp checkout elsewhere, point the script at it to
avoid re-cloning:

```bash
CHORDCRAFT_CHORDMINIAPP_DIR=/path/to/ChordMiniApp bash scripts/prepare_third_party.sh
```

The top-level `.gitignore` ignores everything under `third_party/` except this README.
