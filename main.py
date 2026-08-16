import cv2
import os
from dotenv import load_dotenv  

def main():
    load_dotenv()
    api_key = os.getenv("RTSP_CAT_KEY")
    if not api_key:
        raise RuntimeError("Env var RTSP_CAT_KEY not set. Either add it to .env file in this repo (it's gitignored or export it in your shell temporarily.")

    # On the UDM's own LAN this is 192.168.1.1. Over WireGuard that address is
    # unusable (it collides with the client's own gateway), so use the UDM's
    # VPN-side address 192.168.7.1 instead -- same device, both ports listen.
    host = os.getenv("CAT_HOST", "192.168.1.1")

    RTSPS_URL = f"rtsps://{host}:7441/{api_key}?enableSrtp"
    RTSP_URL = f"rtsp://{host}:7447/{api_key}?enableSrtp"
    cap = cv2.VideoCapture(RTSP_URL)
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
