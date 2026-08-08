#!/usr/bin/env bash
set -euo pipefail

bundle=${1:?usage: install-offline-lfs-assets.sh BUNDLE CHECKOUT}
checkout=${2:?usage: install-offline-lfs-assets.sh BUNDLE CHECKOUT}

test -d "$bundle/objects"
test -d "$checkout/.git"
git -C "$checkout" rev-parse --is-inside-work-tree >/dev/null

assets=(
  "vendor/efficient_sam/efficient_sam_vits_decoder.onnx:4727baf23dacfb51d4c16795b2ac382c403505556d0284e84c6ff3d4e8e36f22:16565728"
  "vendor/efficient_sam/efficient_sam_vits_encoder.onnx:4cacbb23c6903b1acf87f1d77ed806b840800c5fcd4ac8f650cbffed474b8896:89558337"
  "vendor/sam2/checkpoints/sam2.1_hiera_large.pt:2647878d5dfa5098f2f8649825738a9345572bae2d4350a2468587ece47dd318:898083611"
)

for entry in "${assets[@]}"; do
  IFS=: read -r path oid size <<<"$entry"
  pointer=$(git -C "$checkout" show "HEAD:$path")
  grep -qx "oid sha256:$oid" <<<"$pointer"
  grep -qx "size $size" <<<"$pointer"
  object="$bundle/objects/${oid:0:2}/${oid:2:2}/$oid"
  test "$(stat -c %s "$object")" = "$size"
  test "$(sha256sum "$object" | awk '{print $1}')" = "$oid"
  cache="$checkout/.git/lfs/objects/${oid:0:2}/${oid:2:2}"
  install -d -m 0750 "$cache"
  install -m 0440 "$object" "$cache/$oid"
done

git -C "$checkout" lfs checkout -- \
  vendor/efficient_sam/efficient_sam_vits_decoder.onnx \
  vendor/efficient_sam/efficient_sam_vits_encoder.onnx \
  vendor/sam2/checkpoints/sam2.1_hiera_large.pt

for entry in "${assets[@]}"; do
  IFS=: read -r path oid size <<<"$entry"
  test "$(stat -c %s "$checkout/$path")" = "$size"
  test "$(sha256sum "$checkout/$path" | awk '{print $1}')" = "$oid"
done

echo "Offline LFS assets verified and checked out: 3/3"
