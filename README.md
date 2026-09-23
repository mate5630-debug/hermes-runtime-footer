# Hermes Runtime Footer Image

Public, reproducible derived image for the Hostinger Hermes Agent image.

It applies and verifies a small gateway patch at image-build time so Slack runtime footers can include Codex remaining quota and reset timing. The build fails closed if the upstream source anchors drift.

## Image

`ghcr.io/mate5630-debug/hermes-runtime-footer-public:latest`

The image is rebuilt nightly and whenever `main` changes. Both `latest` and immutable commit-SHA tags are published.

## Verification

```bash
python3 -m unittest discover -s tests -v
```

The Docker build also runs `verify_installed.py` against the patched `/opt/hermes` tree before publishing.
