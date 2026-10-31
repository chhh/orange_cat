"""Track the in-progress visit: sample the INSIDE camera until the animal is
gone, so we get a real entry->exit duration. Read-only."""
import os, sys, glob, subprocess, tempfile, time
sys.path.insert(0, "/home/david/projects/ocp"); os.chdir("/home/david/projects/ocp")
import cv2, animal, detect
from capture import measure
SAVE="/home/david/ocp-watch/patrol"
def grab(cam):
    for s in sorted(glob.glob(f"/dev/shm/ocp/{cam}/*.mp4"), key=os.path.getmtime, reverse=True):
        o=tempfile.mktemp(suffix=".jpg")
        if subprocess.run(["ffmpeg","-loglevel","quiet","-y","-i",s,"-frames:v","1","-q:v","2",o],
                          capture_output=True).returncode==0 and os.path.getsize(o)>0:
            f=cv2.imread(o); os.unlink(o)
            if f is not None: return f
    return None
print(f"{time.strftime('%H:%M:%S')} VISIT tracker armed (entry seen 18:48:51)", flush=True)
gone=0; t0=time.time()
while time.time()-t0 < 3600:
    f=grab("inside")
    if f is not None:
        b=animal.best_box(f)
        if b:
            gone=0
            s=measure(f, detect.roi_mask(f.shape,"inside"))
            ir=detect.box_ir_features(f,b["box"]) if s["is_ir"] else None
            v,_,_=detect.classify_detection(detect.box_features(f,b["box"]), ir)
            print(f"{time.strftime('%H:%M:%S')} VISIT inside: {v} conf={b['conf']:.2f} "
                  f"rel_bright={(ir or {}).get('rel_bright')}", flush=True)
        else:
            gone+=1
            if gone==3:
                cv2.imwrite(f"{SAVE}/inside-empty-{time.strftime('%H%M%S')}.jpg", f)
                print(f"{time.strftime('%H:%M:%S')} VISIT ENDED -- inside camera clear for 3 "
                      f"consecutive samples. Entry ~18:48:51.", flush=True)
                break
    time.sleep(20)
