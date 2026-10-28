
import cv2
from ultralytics import YOLO

# General-purpose pretrained model
model = YOLO("yolo26n.pt")

# Capture one frame from your RTSP stream
url = "rtsp://rtsp.rtsplink.com/live/pedestrian-crossing?token=e256ecb78aa5c04393df1f73ea8c904b771a8d1a3b7be8ba545b466bb7a930e6"
cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)

try:
    ret, frame = cap.read()

    if not ret:
        raise RuntimeError("Could not read a video frame")

    # Run object detection
    results = model.predict(
        source=frame,
        conf=0.35,
        verbose=False
    )

    # Save the image with bounding boxes
    annotated = results[0].plot()
    cv2.imwrite("detection_result.jpg", annotated)

    for box in results[0].boxes:
        class_id = int(box.cls[0])
        label = model.names[class_id]
        confidence = float(box.conf[0])

        print(f"{label}: {confidence:.2f}")

    print("Saved detection_result.jpg")

finally:
    cap.release()
