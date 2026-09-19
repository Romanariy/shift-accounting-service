"""Container smoke check: load and infer with every socket connection forbidden."""
import os
from pathlib import Path
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
def blocked(*args, **kwargs):
    raise RuntimeError("Unexpected OCR network connection")
socket.create_connection = blocked
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
from apps.offers.recognizer import LocalOCR
import numpy as np
ocr = LocalOCR(os.environ.get("OFFER_MODEL_DIR", "ocr_models"))
ocr.read(np.full((100, 400, 3), 255, dtype=np.uint8))
print("OK: local models load and infer with sockets blocked")
