# Vendored assets

## silero_vad.onnx

Silero VAD v6 model, ONNX export. MIT licensed (see `LICENSE-silero-vad`),
copyright Silero Team, from <https://github.com/snakers4/silero-vad>.

- Extracted from the `silero-vad` **6.2.1** PyPI wheel
  (`silero_vad/data/silero_vad.onnx`), byte-identical to the file that ships
  next to the `silero_vad.jit` the torch profile runs — both paths therefore
  run the same model generation.
- SHA256: `1A153A22F4509E292A94E67D6F9B85E8DEB25B4988682B7E174C65279D8788E3`
- Size: 2,327,524 bytes
- Consumed by `sublume/core/silero_onnx.py` (the torch-free VAD path).

Why vendored instead of downloaded: the VAD must match the jit model's
generation exactly (A/B parity gate), work offline, and never be a download
failure point; the HuggingFace-only rule governs runtime model downloads,
not repository assets.

### Upgrade procedure

1. `pip download silero-vad==<new version> --no-deps -d <tmp>`
2. Unzip the wheel, take `silero_vad/data/silero_vad.onnx`
3. Re-run the jit-vs-onnx A/B parity check (max per-chunk |delta| <= 1e-4,
   see `tests/unit/test_silero_onnx.py::test_parity_with_jit`)
4. Replace the file, update the version/SHA256/size above
