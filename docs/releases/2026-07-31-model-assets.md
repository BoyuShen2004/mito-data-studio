# Offline AI model release assets

The release requires exactly three Git LFS objects. They are not secrets and
are not production data, but their roughly 1.0 GB of bytes must not be added as
ordinary Git blobs or copied into an unverified checkout.

| Runtime | Repository path | LFS/SHA-256 OID | Bytes | License / provenance |
| --- | --- | --- | ---: | --- |
| EfficientSAM decoder | `vendor/efficient_sam/efficient_sam_vits_decoder.onnx` | `4727baf23dacfb51d4c16795b2ac382c403505556d0284e84c6ff3d4e8e36f22` | 16,565,728 | Apache-2.0; labelmeai/efficient-sam tag `onnx-models-20231225`, commit `6aebcba09318c4dfe2f9560f7a3f8c42d8b01657` |
| EfficientSAM encoder | `vendor/efficient_sam/efficient_sam_vits_encoder.onnx` | `4cacbb23c6903b1acf87f1d77ed806b840800c5fcd4ac8f650cbffed474b8896` | 89,558,337 | Apache-2.0; same release/commit |
| SAM2.1 Hiera Large | `vendor/sam2/checkpoints/sam2.1_hiera_large.pt` | `2647878d5dfa5098f2f8649825738a9345572bae2d4350a2468587ece47dd318` | 898,083,611 | Apache-2.0; facebookresearch/sam2 source commit `2b90b9f5ceec907a1c18123530e92e794ad901a4`, official 2024-09-28 checkpoint URL |

The cutover used a protected offline bundle containing only OID-addressed model
objects and a provenance manifest—no embeddings, masks, source images,
database data, or secrets. The standalone bundle was removed during the
post-release workspace cleanup. The same verified bytes remain in both the
development repository's local Git LFS object cache and the active production
checkout at the repository paths above.

For a future offline clone, first export the three OID-addressed objects from
the development repository's `.git/lfs/objects` cache into a protected bundle
with the layout expected by the installer. Then skip remote smudge and install
from that protected bundle:

```bash
GIT_LFS_SKIP_SMUDGE=1 git clone --no-local SOURCE CHECKOUT
./ops/production/install-offline-lfs-assets.sh \
  PROTECTED_BUNDLE CHECKOUT
```

The installer validates the commit's pointer OID and size, validates the
bundle bytes, populates the clone-local LFS cache, runs `git lfs checkout`, and
validates the resulting working files again.

Validation on 2026-07-31 completed inside a network-disabled namespace:

- pointer-only clone plus local checkout: 3/3 exact hashes;
- staging EfficientSAM encoder and decoder ONNX sessions: loaded;
- staging SAM2.1 large checkpoint and CPU predictor: loaded;
- no network was available to either proof.

Production uses the same repository-default vendor paths today. Its files were
not read, replaced, or modified by this packaging step.
