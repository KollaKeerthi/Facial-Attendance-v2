@echo off
set ENTRY_STREAM_URL=rtmp://127.0.0.1:1935/live/entry
.\.venv\Scripts\python.exe app\face_recognition_demo.py ^
  -i %ENTRY_STREAM_URL% ^
  -m_fd models\models\face-detection-retail-0004\FP32\face-detection-retail-0004.xml ^
  -m_lm models\models\landmarks-regression-retail-0009\FP32\landmarks-regression-retail-0009.xml ^
  -m_reid models\models\face-reidentification-retail-0095\FP32\face-reidentification-retail-0095.xml ^
  -fg my_gallery\my_gallery ^
  --run_detector ^
  --smooth_window 5 ^
  --smooth_min_votes 2 ^
  --min_blur_var 0 ^
  --min_face_size 40 ^
  --no_enroll_augment ^
  --direction in ^
  --camera_name entry-camera ^
  --api_url http://localhost:8000
