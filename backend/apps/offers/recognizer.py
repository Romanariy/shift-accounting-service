"""Local YCLIENTS iOS calendar extraction. OCR scores are evidence, not probabilities."""
import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher

ENGINE_VERSION = "yclients-ios-2"
MONTHS = {name: i + 1 for i, name in enumerate(("января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря"))}
TIME = re.compile(r"(?<!\d)([012]?\d)\s*[:.]\s*([0-5]\d)(?!\d)")


def normalize(value):
    return re.sub(r"[^а-яa-z0-9]", "", unicodedata.normalize("NFKC", value).casefold().replace("ё", "е"))


def times(text):
    return [f"{int(h):02d}:{m}" for h, m in TIME.findall(text) if int(h) < 24]


def nearest_date(day, month, today):
    candidates = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            pass
    if not candidates:
        return None, False
    ranked = sorted(candidates, key=lambda d: abs((d - today).days))
    chosen = ranked[0]
    ambiguous = (chosen < today or abs((chosen - today).days) > 90
                 or len(ranked) > 1 and abs((ranked[0] - today).days) == abs((ranked[1] - today).days))
    return chosen.isoformat(), ambiguous


def match_organization(headers, profiles):
    """A truncated prefix must agree with the profile; a generic shared room never breaks ties."""
    observed = [normalize(h) for h in headers if len(normalize(h)) >= 3]
    candidates = []
    for profile in profiles:
        matched, strong = 0, 0
        for header in observed:
            best = 0
            for room in profile["rooms"]:
                for variant in [room["name"], *room.get("aliases", [])]:
                    name = normalize(variant)
                    if name == header:
                        best = max(best, 1.0)
                    elif len(header) >= 5 and name.startswith(header):
                        best = max(best, .96)
                    elif len(header) >= 5 and SequenceMatcher(None, name, header).ratio() >= .88:
                        best = max(best, .88)
            if best:
                matched += 1
                strong += int(best >= .96)
        if matched and matched == len(observed):
            candidates.append((profile["organization"], strong, matched))
    if len(candidates) == 1 and candidates[0][1] >= 1:
        return candidates[0][0], {"matched_headers": candidates[0][2], "unique": True}
    return None, {"candidates": [c[0] for c in candidates], "unique": False}


class LocalOCR:
    def __init__(self, model_dir, threads=2):
        import os
        import tempfile
        from pathlib import Path
        for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            os.environ[name] = str(threads)
        import cv2
        cv2.setNumThreads(threads)
        os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
        os.environ["PADDLE_PDX_DISABLE_DEV_MODEL_WL"] = "True"
        os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(Path(tempfile.gettempdir()) / "offer-paddlex"))
        root = Path(model_dir)
        det, rec = root / "PP-OCRv5_mobile_det", root / "eslav_PP-OCRv5_mobile_rec"
        for model in (det, rec):
            if not (model / "inference.yml").is_file():
                raise RuntimeError("Модели OCR не установлены. Выполните сборку OCR-образа или download_ocr_models.py.")
        from paddleocr import PaddleOCR
        self.engine = PaddleOCR(text_detection_model_name=det.name, text_detection_model_dir=str(det),
            text_recognition_model_name=rec.name, text_recognition_model_dir=str(rec), device="cpu",
            use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False,
            cpu_threads=threads, enable_mkldnn=True)

    def read(self, image):
        output = []
        for result in self.engine.predict(image):
            for text, score, box in zip(result["rec_texts"], result["rec_scores"], result["rec_boxes"]):
                output.append({"text": text, "score": float(score), "box": [int(n) for n in box]})
        return output


def _rows(mask, minimum):
    import numpy as np
    values = np.flatnonzero(mask.sum(axis=1) >= minimum)
    return [int(round(group.mean())) for group in np.split(values, np.where(np.diff(values) > 2)[0] + 1) if len(group)]


def _room_labels(top, date_bottom, calendar_limit, height, width):
    """Select aligned hall labels above the calendar, regardless of the date/avatar gap."""
    candidates = [t for t in top if t["box"][1] > date_bottom + .025 * height
        and t["box"][3] <= calendar_limit and t["box"][0] > .08 * width
        and not times(t["text"]) and not re.search(r"\+?\d{7}|сегодня|все", t["text"], re.I)
        and len(normalize(t["text"])) >= 3]
    if not candidates:
        return []
    clusters = [[t for t in candidates if abs(t["box"][3] - c["box"][3]) < .014 * height]
                for c in candidates]
    labels = max(clusters, key=lambda group: (len(group), -sum(t["box"][3] for t in group)))
    return sorted(labels, key=lambda t: t["box"][0])


def analyze_image(path, ocr, today):
    import cv2
    import numpy as np
    from PIL import Image
    with Image.open(path) as source:
        rgb = np.asarray(source.convert("RGB"))
    # One canonical width; retain enough detail on the smallest examples without re-encoding.
    scale = 900 / rgb.shape[1]
    image = cv2.resize(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), None, fx=scale, fy=scale)
    h, w = image.shape[:2]
    result = {"date": None, "headers": [], "intervals": [], "issues": [], "header_bottom": None,
              "engine_version": ENGINE_VERSION, "date_ambiguous": True}
    if not 1.6 <= h / w <= 2.6:
        result["issues"].append("unsupported_layout")
        return result
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    # YCLIENTS' full-width announcement banner shifts both date and room headers.
    banner_rows = np.flatnonzero(((hsv[:int(.32*h), :, 1] > 150) & (hsv[:int(.32*h), :, 2] > 150)).mean(axis=1) > .85)
    banner_bottom = int(banner_rows[-1]) + 2 if len(banner_rows) > .025*h else 0
    top_begin = banner_bottom
    top_end = min(int(.44*h), banner_bottom + int(.145*h)) if banner_bottom else int(.205*h)
    top = ocr.read(image[top_begin:top_end])
    for item in top:
        item["box"][1] += top_begin
        item["box"][3] += top_begin
    date_boxes = []
    for item in top:
        for month, number in MONTHS.items():
            found = re.search(r"(\d{1,2})\s*" + month, item["text"].casefold())
            if found:
                result["date"], result["date_ambiguous"] = nearest_date(int(found[1]), number, today)
                result["date_score"] = item["score"]
                result["date_ambiguous"] |= item["score"] < .96
                x1,y1,x2,y2 = item["box"]
                date_area = hsv[max(0,y1-4):min(h,y2+4),max(0,x1-4):min(w,x2+4)]
                # Messenger/viewer overlays can erase a leading digit while OCR remains confident.
                result["date_ambiguous"] |= not date_area.size or float(np.median(date_area[:,:,2])) < 215
                date_boxes.append(item["box"])
    if not date_boxes:
        result["issues"].append("missing_date_header")
    date_bottom = max((box[3] for box in date_boxes), default=top_begin + .045*h if banner_bottom else .093*h)
    # The distance between date and hall names varies across iPhone layouts. Locate the
    # calendar from its left time axis instead of cutting labels at date_bottom + .10*h.
    # This narrow crop never includes customer cards.
    axis_begin = int(date_bottom)
    axis_preview = ocr.read(image[axis_begin:min(h, top_end + int(.10*h)), :int(.12*w)])
    first_tick = min((item["box"][1] + axis_begin for item in axis_preview
                     if len(times(item["text"])) == 1 and item["score"] >= .85), default=top_end)
    calendar_limit = min(top_end, first_tick)
    # Hall labels are below the date and circular avatars, before the first calendar time line.
    def header_background(item):
        x1, y1, x2, y2 = item["box"]
        area = hsv[max(0,y1-3):min(h,y2+3), max(0,x1-3):min(w,x2+3)]
        return area.size and float(np.median(area[:,:,1])) < 22 and float(np.median(area[:,:,2])) > 205
    labels = _room_labels([t for t in top if header_background(t)], date_bottom, calendar_limit, h, w)
    result["headers"] = [t["text"] for t in labels]
    calendar_top = max((t["box"][3] for t in labels), default=date_bottom + .10 * h) + int(.003 * h)
    result["header_bottom"] = int(calendar_top / scale)
    # Fixed bottom app chrome / overlaid messenger chrome must not become event endings.
    calendar_bottom = int(.88 * h)
    dark = (hsv[:, :, 2] < 85).astype(np.uint8)
    navigation_rows = np.flatnonzero(dark[int(.72 * h):int(.94 * h)].sum(axis=1) >= w * .70)
    if len(navigation_rows):
        calendar_bottom = min(calendar_bottom, int(.72 * h) + int(navigation_rows[0]))
    axis = ocr.read(image[int(calendar_top):calendar_bottom, :int(.12 * w)]) if calendar_top < calendar_bottom else []
    anchors = []
    for item in axis:
        ts = times(item["text"])
        if len(ts) == 1 and item["score"] >= .85:
            hour, minute = map(int, ts[0].split(":"))
            anchors.append(((item["box"][1] + item["box"][3]) / 2 + calendar_top, hour * 60 + minute))
    mapping = None
    if len(anchors) >= 2:
        slopes = [(b[1] - a[1]) / (b[0] - a[0]) for a, b in zip(anchors, anchors[1:]) if b[0] > a[0] and b[1] > a[1]]
        if slopes:
            slope = float(np.median(slopes))
            intercept = float(np.median([minute - slope * y for y, minute in anchors]))
            if max(abs(slope * y + intercept - minute) for y, minute in anchors) <= 4:
                mapping = (slope, intercept)
    def geometric(y):
        if mapping is None:
            return None
        minute = mapping[0] * y + mapping[1]
        rounded = round(minute / 5) * 5
        if not 0 <= rounded < 1440 or abs(minute - rounded) > 3:
            return None
        return f"{rounded // 60:02d}:{rounded % 60:02d}"
    # Saturation locates any coloured header, never classifies a booking's business status.
    saturated = ((hsv[:, :, 1] > 75) & (hsv[:, :, 2] > 90)).astype(np.uint8)
    saturated[:int(calendar_top)] = 0
    saturated[calendar_bottom:] = 0
    saturated[:, :int(.11 * w)] = 0
    solid = cv2.morphologyEx(saturated, cv2.MORPH_CLOSE, np.ones((3, 1), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(solid)
    boxes = []
    for x, y, bw, bh, area in stats[1:]:
        if bw < .08 * w or area < bw * min(bh, .018 * h) * .45:
            continue
        if bh < .008 * h:
            if y < calendar_top + .035*h or y + bh > calendar_bottom - .035*h:
                result["issues"].append("partial_booking")
            continue
        if y >= calendar_bottom - 5:
            continue
        boxes.append((int(x), int(y), int(bw), int(bh)))
    boxes.sort(key=lambda b: (b[1], b[0]))
    for x, y, bw, bh in boxes:
        # Read only the short time header, not customer names or phone numbers.
        header_height = int(min(max(.022 * h, 20), bh))
        header = image[max(0, y - 3):min(h, y + header_height), max(0, x - 2):min(w, x + bw + 2)]
        texts = ocr.read(header)
        header_text = " ".join(t["text"] for t in texts)
        found_times = times(header_text)
        if not found_times and bh < .06*h:
            result["issues"].append("unreadable_booking")
            continue
        start = found_times[0] if found_times else None
        end = found_times[1] if len(found_times) > 1 else None
        room = min(labels, key=lambda t: abs((t["box"][0] + t["box"][2]) / 2 - (x + bw / 2)))["text"] if labels else ""
        geometry_start = geometric(y)
        # Follow the card interior downwards. Dark cards and every hue count equally.
        # The floating Today/action controls cover the lower-right calendar. Their white edge
        # cannot be used as an event ending; a fully printed end time can still be used.
        visible_bottom = min(calendar_bottom, int(.775*h)) if x+bw*.8 > .53*w else calendar_bottom
        band = image[y:visible_bottom, x + max(5, bw // 5):x + max(6, 4 * bw // 5)]
        band_hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV) if band.size else None
        bottom, clipped = None, True
        if band_hsv is not None:
            fill = ((band_hsv[:, :, 1] > 30) | (band_hsv[:, :, 2] < 65)).mean(axis=1)
            for i in range(header_height, len(fill) - 4):
                if all(fill[i:i + 4] < .35):
                    bottom, clipped = y + i, False
                    break
        geometry_end = geometric(bottom) if bottom is not None else None
        confidence = min((t["score"] for t in texts if times(t["text"])), default=0)
        issues = []
        if geometry_start and geometry_start != start:
            issues.append("start_conflict")
            start = None
        if end and geometry_end and end != geometry_end:
            issues.append("end_conflict")
            end = None
        elif not end and geometry_end and not clipped:
            end = geometry_end
        if confidence < .93:
            issues.append("uncertain_header")
        if start and end and end <= start:
            issues.append("invalid_interval")
            end = None
        # A component can be a coloured body below a distinct header. De-duplicate contained copies.
        item = {"room": room, "start": start, "end": end, "box": [x / w, y / h, (x + bw) / w,
            (bottom or max(y+header_height, visible_bottom)) / h], "issues": issues,
            "evidence": {"ocr_score": round(confidence, 4), "grid_start": geometry_start, "grid_end": geometry_end,
                         "bottom_clipped": clipped, "text_end": found_times[1] if len(found_times)>1 else None}}
        if not any(old["room"] == room and old["start"] == start
                   and abs(old["box"][1] - item["box"][1]) < .025
                   and abs(old["box"][0] - item["box"][0]) < .02
                   and abs(old["box"][2] - item["box"][2]) < .02 for old in result["intervals"]):
            result["intervals"].append(item)
    # Adjacent bookings may touch with no white gap. A separately detected header is a visible
    # card boundary, unlike a neighbouring hatched block or a service-duration caption.
    for item in result["intervals"]:
        following = [other for other in result["intervals"] if other is not item and
            abs(other["box"][0]-item["box"][0]) < .02 and abs(other["box"][2]-item["box"][2]) < .02
            and item["box"][1]+.025 < other["box"][1] < item["box"][3]-.002]
        if following:
            boundary = min(following, key=lambda i:i["box"][1])["box"][1]*h
            boundary_time = geometric(boundary)
            if boundary_time:
                item["box"][3] = boundary/h
                item["evidence"]["grid_end"] = boundary_time
                if item["evidence"]["text_end"] and item["evidence"]["text_end"] != boundary_time:
                    item["end"] = None
                    item["issues"].append("end_conflict")
                else:
                    item["end"] = boundary_time
    # A sliver of a coloured header immediately above the bottom navigation is still a
    # partially visible booking, even when its height is too small for normal detection.
    edge = hsv[max(int(calendar_top), calendar_bottom-12):calendar_bottom, int(.11*w):]
    if edge.size:
        edge_columns = ((edge[:,:,1]>100)&(edge[:,:,2]>100)).mean(axis=0)>.35
        if edge_columns.sum()>.08*w and not any(i["box"][3]*h>=calendar_bottom-15 for i in result["intervals"]):
            result["issues"].append("partial_booking_at_bottom")
    if not result["intervals"]:
        result["issues"].append("no_readable_bookings")
    return result


def merge_results(results, profiles):
    """Keep each detected booking and its unresolved fields, even if extrema already look plausible."""
    questions, intervals, dates, organizations = [], [], set(), set()
    for index, result in enumerate(results):
        matched_existing = set()
        if result.get("date"):
            dates.add(result["date"])
        org, evidence = match_organization(result.get("headers", []), profiles)
        if org:
            organizations.add(org)
        for item in result.get("intervals", []):
            item = {**item, "image_index": index}
            existing_index = next((number for number, old in enumerate(intervals)
                if number not in matched_existing and old["image_index"] != index
                and normalize(old["room"]) == normalize(item["room"])
                and old.get("start") and old["start"] == item.get("start")
                and (old.get("end") == item.get("end") or not old.get("end") or not item.get("end"))), None)
            if existing_index is not None:
                matched_existing.add(existing_index)
                existing = intervals[existing_index]
                if not existing.get("end") and item.get("end"):
                    existing.update(item)
            else:
                intervals.append(item)
    split_required = len(dates) > 1 or len(organizations) > 1
    if split_required:
        questions.append({"key": "split", "label": "В альбоме разные даты или организации. Выберите изображения для отдельных предложений на сайте.", "type": "split"})
    if len(dates) != 1 or any(r.get("date_ambiguous", True) for r in results):
        questions.append({"key": "date", "label": "Укажите полную дату смены (ДД.ММ.ГГГГ).", "type": "date"})
    if len(organizations) != 1 or any(match_organization(r.get("headers", []), profiles)[0] is None for r in results):
        questions.append({"key": "organization", "label": "Выберите организацию по залам.", "type": "organization"})
    if any(not r.get("intervals") or set(r.get("issues", [])) &
           {"partial_booking", "unreadable_booking", "partial_booking_at_bottom"} for r in results):
        questions.append({"key": "intervals", "label": "Не все записи видны. Укажите интервалы каждой записи через запятую (13:00–16:00, 16:30–20:00) или пришлите новый полный альбом.", "type": "intervals"})
    for index, item in enumerate(intervals):
        for field in ("start", "end"):
            weak = "uncertain_header" in item.get("issues", [])
            grid_agrees = item.get("evidence", {}).get("grid_"+field) == item.get(field) and bool(item.get(field))
            if not item.get(field) or (weak and not grid_agrees):
                questions.append({"key": f"interval:{index}:{field}", "label": f"Запись {index + 1}, {item['room']}: уточните {'начало' if field == 'start' else 'окончание'} (ЧЧ:ММ).", "type": "time"})
    return {"date": next(iter(dates)) if len(dates) == 1 else None,
            "organization": next(iter(organizations)) if len(organizations) == 1 else None,
            "intervals": intervals, "questions": questions,
            "evidence": {"engine_version": ENGINE_VERSION, "auto_candidate": not questions,
                         "images": [{"headers": r.get("headers", []), "issues": r.get("issues", [])} for r in results]}}
