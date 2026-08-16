import cv2
import os
from dotenv import load_dotenv  

def main():
    load_dotenv()
    k1 = "CAT_VIDEO_KEY_INSIDE" 
    k2 = "CAT_VIDEO_KEY_OUTSIDE" 
    api_key_inside = os.getenv(k1)
    api_key_outside = os.getenv(k2)
    if not api_key:
        raise RuntimeError(f"Env vars [{k1}, {k2}] not set. Either add it to .env file in this repo (it's gitignored or export it in your shell temporarily.")

    # RTSPS_URL = f"rtsps://192.168.1.1:7441/{k1}?enableSrtp"
    URL_INSIDE = f"rtsp://192.168.1.1:7447/{k1}?enableSrtp"
    URL_OUTSIDE = f"rtsp://192.168.1.1:7447/{k2}?enableSrtp"
    rtsps://192.168.1.1:7441/QgxffNeM11oM9m6D?enableSrtp
    cap = cv2.VideoCapture(URL_INSIDE)
    if not cap.isOpened():
        raise RuntimeError("Could not open RTSP stream")
    ret, frame = cap.read()
    if not ret or frame is None:
        cap.release()
        raise RuntimeError("Connected, but could not read a frame")

    print(f"Got frame: {frame.shape}")
    cv2.imwrite("frame.jpg", frame)
    print("Saved frame.jpg")
    cap.release()

if __name__ == "__main__":
    main()
