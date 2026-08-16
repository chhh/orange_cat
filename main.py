import cv2
import os
from dotenv import load_dotenv


def main():
    load_dotenv()
    k1 = "CAT_VIDEO_KEY_INSIDE"
    k2 = "CAT_VIDEO_KEY_OUTSIDE"
    api_key_inside = os.getenv(k1)
    api_key_outside = os.getenv(k2)
    if not api_key_inside or not api_key_outside:
        raise RuntimeError(
            f"Env vars [{k1}, {k2}] not set. Add them to .env file (gitignored) "
            "or export in shell."
        )

    # On the UDM's own LAN this is 192.168.1.1. Over WireGuard that address is
    # unusable (it collides with the client's own gateway), so use the UDM's
    # VPN-side address 192.168.7.1 instead -- same device, both ports listen.
    host = os.getenv("CAT_HOST", "192.168.1.1")

    snap(f"rtsp://{host}:7447/{api_key_inside}?enableSrtp", "frame_inside.jpg")
    snap(f"rtsp://{host}:7447/{api_key_outside}?enableSrtp", "frame_outside.jpg")


def snap(url, filename):
    cap = cv2.VideoCapture(url)
    if not cap.isOpened():
        raise RuntimeError("Could not open RTSP stream")
    ret, frame = cap.read()
    if not ret or frame is None:
        cap.release()
        raise RuntimeError("Connected, but could not read a frame")

    print(f"Got frame: {frame.shape}")
    cv2.imwrite(filename, frame)
    print(f"Saved {filename}")
    cap.release()


if __name__ == "__main__":
    main()
