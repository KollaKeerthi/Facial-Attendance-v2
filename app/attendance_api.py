"""Attendance client that sends worker events to the local FastAPI backend."""

import base64
import json
import logging as log
import socket
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib import request

import cv2


class AttendanceApiClient:
    def __init__(self, api_url, direction='in', camera_name=None, stream_url=None,
                 verification_confidence=0.65, send_snapshots=True,
                 spoof_rate_limit_s=5.0):
        self.api_url = api_url.rstrip('/')
        self.direction = direction
        self.camera_name = camera_name or f'{direction}-camera'
        self.stream_url = stream_url
        self.verification_confidence = verification_confidence
        self.send_snapshots = send_snapshots
        self._last_spoof_at = 0.0
        self._spoof_rate_limit_s = spoof_rate_limit_s

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
        req = request.Request(
            self.api_url + path,
            data=data,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            log.info('STEP api_request path=%s bytes=%s', path, len(data))
            with request.urlopen(req, timeout=15) as res:
                body = json.loads(res.read().decode('utf-8'))
                log.info('STEP api_response path=%s result=%s', path, body)
                return body
        except (HTTPError, URLError, TimeoutError, socket.timeout) as exc:
            log.warning('Attendance API request failed: %s', exc)
            return {'ok': False, 'changed': False}

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
        }
        result = self._post('/api/recognition/mark', payload)
        if not result.get('ok'):
            return False, None
        if result.get('changed'):
            log.info('Attendance %s: %s at %s', self.direction.upper(), name, now)
        return bool(result.get('changed')), result.get('time') or now

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
        }
        self._post('/api/recognition/spoof', payload)
        log.warning('Spoof attempt sent from %s camera', self.direction)
        return True

    def close(self):
        return None
