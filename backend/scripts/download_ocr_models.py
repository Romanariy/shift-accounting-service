"""Build-time download only. Production checks the committed SHA-256 manifest."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request

BASE = "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/"
MODELS = ("PP-OCRv5_mobile_det", "eslav_PP-OCRv5_mobile_rec")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", default="ocr_models")
    parser.add_argument("--record-checksums", action="store_true", help="Maintainer-only initial manifest generation")
    args = parser.parse_args()
    manifest_path = Path(__file__).with_name("ocr-models.json")
    manifest = {} if args.record_checksums else json.loads(manifest_path.read_text())
    root = Path(args.directory).resolve()
    for name in MODELS:
        url = BASE + name + "_infer.tar"
        with urllib.request.urlopen(url, timeout=120) as response:
            content = response.read(64 * 1024 * 1024)
        digest = hashlib.sha256(content).hexdigest()
        if not args.record_checksums and digest != manifest[name]["sha256"]:
            raise RuntimeError("Model checksum mismatch: " + name)
        target = root / name
        target.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(content)) as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                # These packages contain flat inference files under one model directory.
                relative = Path(member.name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise RuntimeError("Unexpected archive layout")
                if relative.name not in ("inference.yml", "inference.json", "inference.pdmodel", "inference.pdiparams", "inference.pdiparams.info"):
                    continue
                with archive.extractfile(member) as source:
                    (target / relative.name).write_bytes(source.read())
        manifest[name] = {"url": url, "sha256": digest}
        print(name + " " + digest)
    if args.record_checksums:
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
