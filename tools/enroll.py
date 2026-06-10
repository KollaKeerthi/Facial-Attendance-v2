import argparse
import sys
from pathlib import Path

from openvino import Core

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT / 'app'))
sys.path.append(str(PROJECT_ROOT / 'common_python'))
sys.path.append(str(PROJECT_ROOT / 'common_python' / 'model_zoo'))

from face_detector import FaceDetector
from face_identifier import FaceIdentifier
from faces_database import FacesDatabase
from landmarks_detector import LandmarksDetector


def build_parser():
    parser = argparse.ArgumentParser(
        description='Pre-compute face descriptors and write the gallery cache.'
    )
    parser.add_argument('-fg', '--gallery', default='my_gallery\\my_gallery')
    parser.add_argument('--run_detector', action='store_true')
    parser.add_argument('--no_enroll_augment', action='store_true')
    parser.add_argument('-m_fd', default='models\\models\\face-detection-retail-0004\\FP32\\face-detection-retail-0004.xml')
    parser.add_argument('-m_lm', default='models\\models\\landmarks-regression-retail-0009\\FP32\\landmarks-regression-retail-0009.xml')
    parser.add_argument('-m_reid', default='models\\models\\face-reidentification-retail-0095\\FP32\\face-reidentification-retail-0095.xml')
    parser.add_argument('-d_fd', default='AUTO:GPU,CPU')
    parser.add_argument('-d_lm', default='AUTO:GPU,CPU')
    parser.add_argument('-d_reid', default='AUTO:GPU,CPU')
    parser.add_argument('-t_id', type=float, default=0.3)
    return parser


def main():
    args = build_parser().parse_args()
    core = Core()
    face_detector = FaceDetector(core, Path(args.m_fd), (0, 0), confidence_threshold=0.6)
    landmarks_detector = LandmarksDetector(core, Path(args.m_lm))
    face_identifier = FaceIdentifier(core, Path(args.m_reid), match_threshold=args.t_id)

    face_detector.deploy(args.d_fd)
    landmarks_detector.deploy(args.d_lm, 16)
    face_identifier.deploy(args.d_reid, 16)

    db = FacesDatabase(
        args.gallery,
        face_identifier,
        landmarks_detector,
        face_detector if args.run_detector else None,
        no_show=True,
        augment=not args.no_enroll_augment,
    )
    face_identifier.set_faces_database(db)
    print(f'Enrollment cache ready: {len(db)} identities in {args.gallery}')


if __name__ == '__main__':
    main()
