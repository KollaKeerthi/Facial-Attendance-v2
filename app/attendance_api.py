"""Attendance client that sends worker events to the local FastAPI backend."""

import base64
import json
import logging as log
import queue
import socket
import threading
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib import request

import cv2


class AttendanceApiClient:
    def __init__(self, api_url, direction='in', camera_name=None, stream_url=None,
                 verification_confidence=0.65, send_snapshots=True,
                 spoof_rate_limit_s=5.0, queue_size=128, api_timeout_s=3.0,
                 company_id=None, worker_secret=None):
        self.api_url = api_url.rstrip('/')
        self.direction = direction
        self.camera_name = camera_name or f'{direction}-camera'
        self.stream_url = stream_url
        self.verification_confidence = verification_confidence
        self.send_snapshots = send_snapshots
        self.company_id = company_id
        self.worker_secret = worker_secret
        self._last_spoof_at = 0.0
        self._spoof_rate_limit_s = spoof_rate_limit_s
        self._api_timeout_s = api_timeout_s
        self._queue = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run, name=f'attendance-api-{self.camera_name}', daemon=True)
        self._worker.start()

    def _crop_face(self, frame, roi):
        x1 = max(int(roi.position[0]), 0)
        y1 = max(int(roi.position[1]), 0)
        x2 = min(int(roi.position[0] + roi.size[0]), frame.shape[1])
        y2 = min(int(roi.position[1] + roi.size[1]), frame.shape[0])
        if x2 <= x1 or y2 <= y1:
            return None
        return frame[y1:y2, x1:x2]

    def _snapshot_base64(self, frame, roi):
        crop = self._crop_face(frame, roi)
        if crop is None:
            return None
        ok, encoded = cv2.imencode('.jpg', crop)
        if not ok:
            return None
        return base64.b64encode(encoded.tobytes()).decode('ascii')

    def _post(self, path, payload):
        data = json.dumps(payload).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        if self.worker_secret:
            headers['X-Worker-Secret'] = self.worker_secret
        req = request.Request(
            self.api_url + path,
            data=data,
            headers=headers,
            method='POST',
        )
        try:
            log.info('STEP api_request path=%s bytes=%s', path, len(data))
            with request.urlopen(req, timeout=self._api_timeout_s) as res:
                body = json.loads(res.read().decode('utf-8'))
                log.info('STEP api_response path=%s result=%s', path, body)
                return body
        except (HTTPError, URLError, TimeoutError, socket.timeout) as exc:
            log.warning('Attendance API request failed: %s', exc)
            return {'ok': False, 'changed': False}

    def _enqueue(self, path, payload, retry=True):
        try:
            self._queue.put_nowait((path, payload, retry))
            return True
        except queue.Full:
            log.warning('Attendance API queue full; dropping %s event for %s', path, self.camera_name)
            return False

    def _run(self):
        while not self._stop.is_set() or not self._queue.empty():
            try:
                path, payload, retry = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                result = self._post(path, payload)
                if retry and not result.get('ok'):
                    time.sleep(0.25)
                    self._post(path, payload)
            finally:
                self._queue.task_done()

    def queue_size(self):
        return self._queue.qsize()

    def mark(self, name, confidence, frame, roi):
        now = datetime.now().strftime('%H:%M:%S')
        payload = {
            'name': name,
            'confidence': float(confidence),
            'direction': self.direction,
            'camera_name': self.camera_name,
            'stream_url': self.stream_url,
            'verification_confidence': self.verification_confidence,
            'snapshot_base64': self._snapshot_base64(frame, roi) if self.send_snapshots else None,
            'company_id': self.company_id,
        }
        queued = self._enqueue('/api/recognition/mark', payload)
        if queued:
            log.info('Attendance %s queued: %s at %s', self.direction.upper(), name, now)
        return queued, now

    def log_spoof(self, frame, roi):
        now_mono = time.monotonic()
        if now_mono - self._last_spoof_at < self._spoof_rate_limit_s:
            return False
        self._last_spoof_at = now_mono
        payload = {
            'direction': self.direction,
            'camera_name': self.camera_name,
            'stream_url': self.stream_url,
            'snapshot_base64': self._snapshot_base64(frame, roi) if self.send_snapshots else None,
            'company_id': self.company_id,
        }
        queued = self._enqueue('/api/recognition/spoof', payload)
        if queued:
            log.warning('Spoof attempt queued from %s camera', self.direction)
        return queued

    def heartbeat(self, fps=None, inference_ms=None, reconnect_count=0,
                  last_frame_at=None, status='online', status_message=''):
        payload = {
            'name': self.camera_name,
            'direction': self.direction,
            'stream_url': self.stream_url,
            'fps': fps,
            'inference_ms': inference_ms,
            'api_queue_size': self.queue_size(),
            'reconnect_count': reconnect_count,
            'last_frame_at': last_frame_at,
            'status': status,
            'status_message': status_message,
            'company_id': self.company_id,
        }
        return self._enqueue('/api/cameras/heartbeat', payload, retry=False)

    def close(self):
        self._stop.set()
        self._worker.join(timeout=2.0)
