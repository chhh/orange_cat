from flask import Flask
import os
import cv2
import time
from dotenv import load_dotenv  

app = Flask(__name__)


load_dotenv()
api_key = os.getenv("RTSP_CAT_KEY")
if not api_key:
    raise RuntimeError("Env var RTSP_CAT_KEY not set. Either add it to .env file in this repo (it's gitignored or export it in your shell temporarily.")
RTSP_URL = f"rtsp://192.168.1.1:7447/{api_key}"

@app.post("/motion")
def motion():
    print("Motion detected!")

    cap = cv2.VideoCapture(RTSP_URL)

    if not cap.isOpened():
        return {"ok": False, "error": "Could not open stream"}, 500

    # Give the stream a moment to produce a current frame
    for _ in range(5):
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)

    cap.release()

    if not ret:
        return {"ok": False, "error": "Could not read frame"}, 500

    filename = f"motion-{int(time.time())}.jpg"
    cv2.imwrite(filename, frame)

    print(f"Saved {filename}")
    return {"ok": True, "file": filename}


app.run(host="0.0.0.0", port=5000)
