"""Local archive evaluation. Never writes raw OCR text, names or phone numbers."""
import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apps.offers.recognizer import LocalOCR, analyze_image, merge_results, ENGINE_VERSION


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive")
    parser.add_argument("--output", default=".local/ocr-eval")
    parser.add_argument("--models", default="backend/ocr_models")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--today", default=date.today().isoformat())
    parser.add_argument("--offline", action="store_true", help="Reject all socket connection attempts, including model downloads")
    parser.add_argument("--pair", default="18,20", help="Two 1-based archive image indices for an actual warm pair probe")
    args = parser.parse_args()
    if args.offline:
        import socket
        def blocked(*args, **kwargs):
            raise RuntimeError("Network access is forbidden during OCR evaluation")
        socket.create_connection = blocked
        socket.socket.connect = blocked
        socket.socket.connect_ex = blocked
    target = Path(args.output)
    target.mkdir(parents=True, exist_ok=True)
    began = time.perf_counter()
    import psutil
    process = psutil.Process()
    cpu_began = sum(process.cpu_times()[:2])
    ocr = LocalOCR(args.models)
    startup = time.perf_counter() - began
    peak_rss = process.memory_info().rss
    results = []
    with zipfile.ZipFile(args.archive) as archive:
        members = [m for m in archive.infolist() if m.filename.lower().endswith((".jpg", ".png", ".jpeg")) and m.file_size < 10_000_000]
        for index, member in enumerate(members[:args.limit or None]):
            content = archive.read(member)
            path = target / f"{index + 1:02d}.jpg"
            path.write_bytes(content)
            began = time.perf_counter()
            result = analyze_image(path, ocr, date.fromisoformat(args.today))
            result.update(index=index + 1, sha256=hashlib.sha256(content).hexdigest(), seconds=round(time.perf_counter() - began, 3))
            results.append(result)
            peak_rss = max(peak_rss, process.memory_info().rss)
            print(json.dumps({k: result[k] for k in ("index", "date", "headers", "intervals", "seconds")}, ensure_ascii=True), flush=True)
            report = {"engine_version": ENGINE_VERSION, "platform": platform.platform(), "cpu": platform.processor(),
                "evaluation_today":args.today,
                "threads": 2, "startup_seconds": round(startup, 3), "images": results,
                "median_seconds": statistics.median(r["seconds"] for r in results), "independent": False,
                "offline": args.offline, "rss_mb": round(peak_rss/1024/1024,1), "cpu_seconds": round(sum(process.cpu_times()[:2])-cpu_began,2),
                "precision": None, "coverage": None, "note": "Development set; no independent ground truth or accuracy claim."}
            (target / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    pair = [int(i) for i in args.pair.split(",")]
    if len(pair) == 2 and all(1 <= i <= len(results) for i in pair):
        profiles = json.loads(Path(__file__).with_name("ds-reference.json").read_text(encoding="utf-8"))["profiles"]
        began = time.perf_counter()
        recognized = [analyze_image(target / f"{i:02d}.jpg", ocr, date.fromisoformat(args.today)) for i in pair]
        merged = merge_results(recognized, profiles)
        report["two_image_probe"] = {"indices":pair,"seconds":round(time.perf_counter()-began,3),
            "questions":[q["key"] for q in merged["questions"]],"intervals":len(merged["intervals"]),
            "note":"Warm local OCR plus merge; excludes Telegram download, queue wait and reply delivery."}
        report["cpu_seconds"] = round(sum(process.cpu_times()[:2])-cpu_began,2)
        report["rss_mb"] = round(max(peak_rss, process.memory_info().rss)/1024/1024,1)
        (target / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report["two_image_probe"],ensure_ascii=True),flush=True)


if __name__ == "__main__":
    main()
