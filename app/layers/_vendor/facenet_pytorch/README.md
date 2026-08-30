# Vendored from facenet-pytorch

The files in this directory (`models/mtcnn.py`, `models/inception_resnet_v1.py`,
`models/utils/detect_face.py`, `models/utils/download.py`) are copied,
essentially unmodified, from [timesler/facenet-pytorch](https://github.com/timesler/facenet-pytorch)
v2.6.0, under the MIT license in `LICENSE.md`.

## Why vendored instead of a normal dependency

facenet-pytorch's latest PyPI release pins `torch<2.3.0`, `numpy<2.0.0`, and
`Pillow<10.3.0` -- versions with no prebuilt wheels for recent Python, which
would force a broken from-source build (or a downgrade of the torch/numpy
already required by the real deepfake layer) if installed normally. The
actual code works fine with much newer versions; vendoring these specific
files avoids the packaging problem entirely while keeping the real,
unmodified model implementation.

## Changes from upstream

- `inception_resnet_v1.py`: removed the unused `import requests` /
  `from requests.adapters import HTTPAdapter` lines -- dead imports in the
  original (the actual weight download uses `download_url_to_file` /
  `urllib`, not `requests`). No behavioral change. `InceptionResnetV1`'s own
  pretrained weights (vggface2/casia-webface) were already downloaded from a
  public URL upstream, not bundled -- unchanged here, still cached under
  `TORCH_HOME`.
- `mtcnn.py`: PNet/RNet/ONet weights (~2MB total) ship as bundled package
  data upstream (`facenet_pytorch/data/*.pt`, installed alongside the code).
  Committing binary weight files to this repo isn't a good idea, so they are
  fetched at runtime instead -- same pattern as every other model weight in
  this project -- from the same files' stable location in the upstream
  GitHub repo (`github.com/timesler/facenet-pytorch/blob/master/data/`),
  verified byte-identical (sha256) to the ones bundled in the PyPI wheel, and
  hash-checked again on every download via `download_url_to_file`'s
  `hash_prefix`. Cached under the same `TORCH_HOME`-based path as everything
  else. No behavioral change, just where the three small files come from.
