# Model Checkpoints

This directory holds locally-downloaded model weights that are gitignored (too
large to commit, and re-downloadable). RF-DETR's pretrained weights are
downloaded automatically on first use by the `rfdetr` package itself (cached
under `~/.roboflow/models/`), so nothing to do there.

## SAM2 checkpoint (required for the `tracking` stage)

The `sam2` pip package does **not** bundle model weights - they're Meta's
separate release. Download the tiny checkpoint (matches
`SAM2_CONFIG_FILE=configs/sam2.1/sam2.1_hiera_t.yaml`, the Phase 1 default in
`config/settings.py`):

```bash
curl -L -o models/sam2.1_hiera_tiny.pt \
  https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt
```

Other sizes (larger = more accurate, slower) are at the same base URL with
`sam2.1_hiera_{small,base_plus,large}.pt`; if you switch sizes, update
`SAM2_CONFIG_FILE` to match (`configs/sam2.1/sam2.1_hiera_{s,b+,l}.yaml`).

`SAM2_CHECKPOINT_PATH` and `SAM2_CONFIG_FILE` in `config/settings.py` can be
overridden via environment variables if you keep the checkpoint elsewhere.
