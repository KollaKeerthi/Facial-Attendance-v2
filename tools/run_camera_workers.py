import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def flag(args, enabled, name):
    if enabled:
        args.append(name)


def build_command(camera, defaults, models):
    python_exe = PROJECT_ROOT / '.venv' / 'Scripts' / 'python.exe'
    if not python_exe.exists():
        python_exe = Path(sys.executable)

    get = lambda key, default=None: camera.get(key, defaults.get(key, default))
    cmd = [
        str(python_exe),
        'app\\face_recognition_demo.py',
        '-i', get('rtsp_url'),
        '-m_fd', models['face_detection'],
        '-m_lm', models['landmarks'],
        '-m_reid', models['reidentification'],
        '-fg', get('gallery'),
        '--run_detector',
        '--smooth_window', str(get('smooth_window', 5)),
        '--smooth_min_votes', str(get('smooth_min_votes', 2)),
        '--min_blur_var', str(get('min_blur_var', 0)),
        '--min_face_size', str(get('min_face_size', 40)),
        '--process_every', str(get('process_every', 2)),
        '--stale_frame_grabs', str(get('stale_frame_grabs', 4)),
        '-d_fd', get('device_fd', 'AUTO:GPU,CPU'),
        '-d_lm', get('device_lm', 'AUTO:GPU,CPU'),
        '-d_reid', get('device_reid', 'AUTO:GPU,CPU'),
        '--direction', get('direction'),
        '--camera_name', get('name'),
        '--api_url', get('api_url'),
        '--company_id', str(get('company_id', 1)),
        '--attendance_cooldown', str(get('attendance_cooldown', 45)),
        '--log_file', get('log_file', f"logs\\{get('name')}_worker.log"),
        '--reconnect',
        '--no_show',
        '--no_enroll_augment',
    ]
    flag(cmd, get('drop_stale_frames', True), '--drop_stale_frames')
    flag(cmd, get('no_event_snapshots', True), '--no_event_snapshots')
    if get('worker_secret'):
        cmd.extend(['--worker_secret', get('worker_secret')])
    if get('anti_spoof_model'):
        cmd.extend(['-m_as', get('anti_spoof_model')])
        cmd.extend(['-d_as', get('device_as', get('device_reid', 'AUTO:GPU,CPU'))])
    return cmd


def load_config(path):
    with path.open('r', encoding='utf-8') as fh:
        config = json.load(fh)
    return config.get('defaults', {}), config['models'], config['cameras']


def main():
    parser = argparse.ArgumentParser(description='Launch configured CCTV attendance workers.')
    parser.add_argument('--config', default='config\\cameras.json')
    parser.add_argument('--only', nargs='*', help='Optional camera names to launch')
    parser.add_argument('--print-only', action='store_true', help='Print worker commands without launching them')
    args = parser.parse_args()

    config_path = (PROJECT_ROOT / args.config).resolve()
    defaults, models, cameras = load_config(config_path)
    selected = [c for c in cameras if not args.only or c.get('name') in args.only]
    if not selected:
        raise SystemExit('No cameras selected')

    (PROJECT_ROOT / 'logs').mkdir(exist_ok=True)
    processes = []
    try:
        for camera in selected:
            cmd = build_command(camera, defaults, models)
            print(f"Starting {camera['name']} ({camera['direction']}): {camera['rtsp_url']}")
            if args.print_only:
                print(subprocess.list2cmdline(cmd))
                continue
            processes.append(subprocess.Popen(cmd, cwd=PROJECT_ROOT))
            time.sleep(0.75)
        if args.print_only:
            return
        print('Camera workers are running. Press Ctrl+C to stop them.')
        while True:
            for proc, camera in list(zip(processes, selected)):
                if proc.poll() is not None:
                    raise SystemExit(f"Worker {camera['name']} exited with code {proc.returncode}")
            time.sleep(2)
    except KeyboardInterrupt:
        print('Stopping camera workers...')
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
        for proc in processes:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == '__main__':
    main()
