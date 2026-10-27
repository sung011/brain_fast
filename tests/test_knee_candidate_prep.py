"""
무릎 후보·검수 도구(class/knee/candidate_prep.py) 시험. 합성 볼륨만 쓴다(실제 KneeMRI 영상·실제 환자 정보 없음).

핵심: 추출한 PNG 와 화면에 그릴 박스가 **같은 변환**을 쓰는지를 일부러 비대칭인 합성 영상으로 확인한다
(전치·뒤집기·회전·0/1 기반 번호가 어긋나면 바로 드러나게). 후보만 만들고 승인하지 않는다는 것, 서버 코드가 이 도구를 불러오지 않는다는 것도 확인한다.
실행: python -m pytest tests/test_knee_candidate_prep.py   (numpy, pillow 필요)
"""
import io
import json
import os
import pickle
import re
import subprocess
import sys
import tempfile
import unittest
from importlib import import_module
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

prep = import_module("class.knee.candidate_prep")
slide_refs = import_module("class.knee.slide_references")
ROOT = Path(__file__).resolve().parents[1]

NZ, H, W = 12, 60, 90                       # 일부러 정사각형이 아니다(가로≠세로)
BOX = dict(x=30, y=10, width=20, height=15)  # 가로 20 ≠ 세로 15


def synthetic_volume(roi_z=4, roi_depth=4):
    """z 마다 (0,0) 픽셀 값이 다르고, ROI 안에는 왼쪽 위에 어두운 홈이 있는 밝은 블록이 있는 합성 볼륨(int16, 12비트 범위)."""
    volume = np.full((NZ, H, W), 300, dtype=np.int16)
    for z in range(NZ):
        volume[z, 0, 0] = 100 + z * 300                    # 이 값으로 "몇 번째 z 인가"를 알아낸다(0 기반 번호 확인)
    for z in range(roi_z, roi_z + roi_depth):
        volume[z, BOX["y"]:BOX["y"] + BOX["height"], BOX["x"]:BOX["x"] + BOX["width"]] = 4000   # 밝은 블록
        volume[z, BOX["y"]:BOX["y"] + 4, BOX["x"]:BOX["x"] + 4] = 150                            # 왼쪽 위 홈(비대칭)
    return volume


def meta_row(roi_z=4, roi_depth=4, **over):
    fields = dict(exam_id=111111, series_no=7, acl_diagnosis=2, knee_lr=1, roi_x=BOX["x"], roi_y=BOX["y"], roi_z=roi_z,
                  roi_height=BOX["height"], roi_width=BOX["width"], roi_depth=roi_depth, volume_filename="111111-7.pck")
    fields.update(over)
    return prep.MetaRow(**fields)


def decode(png: bytes) -> np.ndarray:
    from PIL import Image
    return np.array(Image.open(io.BytesIO(png)).convert("L"))


class TransformAlignmentTests(unittest.TestCase):
    """추출 PNG 와 박스가 같은 변환이어야 한다(비대칭 합성 영상)."""

    def setUp(self):
        self.volume = synthetic_volume()
        self.case = prep.build_case(self.volume, meta_row(), "case-001", count=12)
        self.rep = self.case.slices[self.case.candidate["representative_slice_no"]]
        self.image = decode(self.rep["png"])

    def bright(self, image):
        mask = image > 200
        ys, xs = np.where(mask)
        return xs.min(), ys.min(), xs.max() - xs.min() + 1, ys.max() - ys.min() + 1

    def test_png_has_the_source_slice_size_and_no_resize(self):
        self.assertEqual(self.image.shape, (H, W))                              # (세로, 가로) = 원본 한 장 그대로
        self.assertEqual((self.rep["width"], self.rep["height"]), (W, H))
        self.assertEqual((self.case.candidate["image_width"], self.case.candidate["image_height"]), (W, H))

    def test_bright_block_sits_exactly_in_the_normalized_box(self):
        box = self.case.candidate["box"]
        x, y, w, h = round(box["x"] * W), round(box["y"] * H), round(box["width"] * W), round(box["height"] * H)
        self.assertEqual((x, y, w, h), (BOX["x"], BOX["y"], BOX["width"], BOX["height"]))
        left, top, width, height = self.bright(self.image)
        # 홈(왼쪽 위 4×4)은 어두우므로 밝은 영역의 바깥 테두리는 블록 전체와 같다(홈이 한 모서리에만 있어서)
        self.assertEqual((left, top, width, height), (BOX["x"], BOX["y"] + 0, BOX["width"], BOX["height"]))
        inside = self.image[y:y + h, x:x + w]
        self.assertGreater((inside > 200).mean(), 0.9)                          # 박스 안은 거의 밝다
        outside = self.image.copy()
        outside[y:y + h, x:x + w] = 0
        self.assertLess((outside > 200).sum(), 4)                               # 박스 밖은 밝지 않다

    def test_the_notch_is_at_the_top_left_so_no_flip_or_transpose(self):
        x, y = BOX["x"], BOX["y"]
        self.assertLess(int(self.image[y + 1, x + 1]), 100)                    # 왼쪽 위 홈은 어둡다
        self.assertGreater(int(self.image[y + BOX["height"] - 2, x + BOX["width"] - 2]), 200)   # 오른쪽 아래는 밝다
        self.assertGreater(int(self.image[y + BOX["height"] - 2, x + 1]), 200)  # 왼쪽 아래도 밝다(좌우·상하 뒤집힘 확인)
        self.assertGreater(int(self.image[y + 1, x + BOX["width"] - 2]), 200)

    def test_the_check_is_sensitive_to_wrong_transforms(self):
        """잘못된 변환(전치·상하·좌우 뒤집기·회전)이면 위 검사가 실패해야 의미가 있다."""
        wrong = {"transpose": self.image.T, "flip_ud": self.image[::-1, :], "flip_lr": self.image[:, ::-1], "rot90": np.rot90(self.image)}
        for name, image in wrong.items():
            with self.subTest(wrong=name):
                ok_size = image.shape == (H, W)
                notch_ok = ok_size and int(image[BOX["y"] + 1, BOX["x"] + 1]) < 100 and int(image[BOX["y"] + BOX["height"] - 2, BOX["x"] + BOX["width"] - 2]) > 200 \
                    and int(image[BOX["y"] + BOX["height"] - 2, BOX["x"] + 1]) > 200 and int(image[BOX["y"] + 1, BOX["x"] + BOX["width"] - 2]) > 200
                self.assertFalse(notch_ok)

    def test_zero_based_numbers_and_source_z_mapping(self):
        # 모서리 값 100..3400 이 창 밖으로 잘리지 않도록 이 시험에서는 최소~최대 창(0~100%)을 쓴다
        self.case = prep.build_case(self.volume, meta_row(), "case-001", count=12, window_pct=(0.0, 100.0))
        zs = [s["source_z"] for s in self.case.slices]
        self.assertEqual(zs, list(range(12)))                                   # 전부 포함(12장)
        self.assertEqual(self.case.candidate["representative_source_z"], 4 + 4 // 2)   # roiZ + roiDepth // 2 = 6
        self.assertEqual(self.case.candidate["representative_slice_no"], 6)     # 등록 배열 위치(0 기반)
        # (0,0) 픽셀 값이 z 에 대해 증가한다 → PNG 가 올바른 z 장에서 나왔다
        corner = [int(decode(s["png"])[0, 0]) for s in self.case.slices]
        self.assertEqual(corner, sorted(corner))
        self.assertEqual(len(set(corner)), len(corner))

    def test_same_window_for_every_slice_so_slices_are_comparable(self):
        lo, hi = self.case.window["lo"], self.case.window["hi"]
        for s in self.case.slices:
            expected = np.clip(np.rint((self.volume[s["source_z"]].astype(float) - lo) / (hi - lo) * 255), 0, 255).astype(np.uint8)
            self.assertTrue(np.array_equal(decode(s["png"]), expected), s["source_z"])


class CandidateSelectionTests(unittest.TestCase):
    def test_middle_candidate_for_odd_and_even_depth(self):
        for depth, expected in ((3, 5), (4, 6), (1, 4), (2, 5)):
            volume = synthetic_volume(roi_z=4, roi_depth=depth)
            case = prep.build_case(volume, meta_row(roi_z=4, roi_depth=depth), "case-001", count=12)
            self.assertEqual(case.candidate["representative_source_z"], 4 + depth // 2, depth)
            self.assertEqual(case.candidate["representative_source_z"], expected)

    def test_slice_window_contains_the_candidate_and_stays_inside(self):
        self.assertEqual(prep.choose_slices(6, 12, 24), list(range(12)))       # 볼륨보다 많이 달라고 하면 전부
        self.assertEqual(prep.choose_slices(0, 30, 10), list(range(0, 10)))    # 앞쪽 끝에서는 잘라 맞춘다(후보 포함)
        self.assertEqual(prep.choose_slices(29, 30, 10), list(range(20, 30)))
        picked = prep.choose_slices(15, 40, 8)
        self.assertIn(15, picked)
        self.assertEqual(len(picked), 8)
        self.assertEqual(picked, sorted(picked))
        for bad in ((-1, 10, 5), (10, 10, 5), (3, 0, 5), (3, 10, 0)):
            with self.assertRaises(prep.CandidateError):
                prep.choose_slices(*bad)

    def test_bad_ranges_and_boxes_are_rejected_not_fixed(self):
        volume = synthetic_volume()
        bad_rows = {
            "z range past the end": meta_row(roi_z=10, roi_depth=4),            # 10+4 > 12
            "negative z": meta_row(roi_z=-1),
            "zero depth": meta_row(roi_depth=0),
            "box past the right edge": meta_row(roi_x=80),                      # 80+20 > 90
            "box past the bottom edge": meta_row(roi_y=50),                     # 50+15 > 60
            "zero width": meta_row(roi_width=0),
            "negative x": meta_row(roi_x=-1),
        }
        for name, row in bad_rows.items():
            with self.subTest(name=name):
                with self.assertRaises(prep.CandidateError):
                    prep.build_case(volume, row, "case-001", count=12)

    def test_bad_volumes_and_codes(self):
        for bad in (np.zeros((5, 5), dtype=np.int16), np.zeros((2, 2, 2, 2)), np.zeros((3, 4, 4), dtype=bool), np.full((3, 4, 4), np.nan),
                    np.zeros((600, 4, 4), dtype=np.int16), [[1]]):
            with self.assertRaises(prep.CandidateError):
                prep.check_volume(bad)
        with self.assertRaises(prep.CandidateError):
            prep.build_case(synthetic_volume(), meta_row(), "case-1")
        with self.assertRaises(prep.CandidateError):
            prep.build_case(synthetic_volume(), meta_row(), "../x")

    def test_window_handles_a_constant_volume(self):
        flat = np.full((3, 4, 4), 77, dtype=np.int16)
        lo, hi = prep.volume_window(flat)
        self.assertGreater(hi, lo)
        self.assertTrue((prep.to_uint8(flat[0], lo, hi) == 0).all())
        for bad in ((50, 10), (-1, 99), (0, 101)):
            with self.assertRaises(prep.CandidateError):
                prep.volume_window(flat, *bad)


class MetadataTests(unittest.TestCase):
    CSV = ("examId,seriesNo,aclDiagnosis,kneeLR,roiX,roiY,roiZ,roiHeight,roiWidth,roiDepth,volumeFilename\n"
           "329637,8,0,1,139,184,14,74,72,3,329637-8.pck\n")

    def test_reads_by_column_name_height_before_width(self):
        row = prep.parse_metadata(self.CSV)[0]
        self.assertEqual((row.roi_x, row.roi_y, row.roi_z, row.roi_height, row.roi_width, row.roi_depth), (139, 184, 14, 74, 72, 3))
        self.assertNotEqual(row.roi_height, row.roi_width)                      # 열 순서(높이→너비)를 이름으로 구분한다

    def test_column_order_does_not_matter(self):
        shuffled = ("volumeFilename,roiDepth,roiWidth,roiHeight,roiZ,roiY,roiX,kneeLR,aclDiagnosis,seriesNo,examId\n"
                    "329637-8.pck,3,72,74,14,184,139,1,0,8,329637\n")
        self.assertEqual(prep.parse_metadata(shuffled), prep.parse_metadata(self.CSV))

    def test_bad_metadata_is_rejected(self):
        for text in (self.CSV.replace("examId", "id"), self.CSV.replace("139", "13.9"), self.CSV.replace("139", "abc"),
                     self.CSV.replace("329637-8.pck", "../329637-8.pck"), self.CSV.replace("329637-8.pck", "a/b.pck"), self.CSV.replace("329637-8.pck", "x.exe"),
                     self.CSV.replace("329637-8.pck", "")):
            with self.assertRaises(prep.CandidateError):
                prep.parse_metadata(text)


class PickleSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_requires_the_explicit_trust_flag(self):
        path = self.dir / "v.pck"
        path.write_bytes(pickle.dumps(synthetic_volume()))
        for value in (False, None, 1, "yes"):
            with self.assertRaises(prep.CandidateError):
                prep.load_volume_trusted(path, trusted_local=value)
        with self.assertRaises(prep.CandidateError):
            prep.load_volume_trusted(path)

    def test_loads_a_numpy_volume(self):
        path = self.dir / "v.pck"
        volume = synthetic_volume()
        path.write_bytes(pickle.dumps(volume))
        loaded = prep.load_volume_trusted(path, trusted_local=True)
        self.assertTrue(np.array_equal(loaded, volume))

    def test_refuses_objects_other_than_arrays(self):
        class Evil:
            def __reduce__(self):
                import os
                return (os.getcwd, ())          # 무해한 호출이지만 허용 목록에 없다 → 거절돼야 한다
        path = self.dir / "evil.pck"
        path.write_bytes(pickle.dumps(Evil()))
        with self.assertRaises(pickle.UnpicklingError):
            prep.load_volume_trusted(path, trusted_local=True)
        other = self.dir / "dict.pck"
        other.write_bytes(pickle.dumps({"a": 1}))
        with self.assertRaises(prep.CandidateError):
            prep.load_volume_trusted(other, trusted_local=True)


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "out"
        self.case = prep.build_case(synthetic_volume(), meta_row(), "case-001", count=12)
        self.report = prep.write_cases([self.case], self.out)

    def test_files_are_written_and_not_overwritten(self):
        self.assertTrue((self.out / "case-001" / "slice_006.png").exists())
        self.assertTrue((self.out / "case-001" / "candidate.json").exists())
        self.assertTrue((self.out / "review_sheet.html").exists())
        with self.assertRaises(prep.CandidateError):
            prep.write_cases([self.case], self.out)                              # 이미 내용이 있는 폴더에는 쓰지 않는다

    def test_shareable_candidate_has_no_patient_link_or_label(self):
        shared = (self.out / "case-001" / "candidate.json").read_text(encoding="utf-8")
        for secret in ("111111", "111111-7", ".pck", "acl_diagnosis", "aclDiagnosis", "exam", "series", "kneeLR"):
            self.assertNotIn(secret, shared, secret)
        data = json.loads(shared)
        self.assertEqual(data["status"], "CANDIDATE_ONLY_NOT_APPROVED")
        self.assertEqual(data["plane"], "sagittal")
        self.assertEqual(data["dataset"]["license"], "CC BY-NC-ND 4.0")
        self.assertIn("Clinical Hospital Centre Rijeka", data["dataset"]["source"])
        self.assertIn("10.1016/j.cmpb.2016.12.006", data["dataset"]["paper"])
        self.assertEqual(data["orientation"]["transpose"], False)
        self.assertIn("임상 검증 아님", data["purpose"])
        self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", data["fingerprint"]))
        for item in data["slices"]:
            self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
            self.assertNotIn(str(self.out), item["file"])                         # 실제 경로를 넣지 않는다

    def test_shareable_candidate_has_exactly_the_allowed_fields(self):
        """허용 목록: 새 필드가 몰래 끼어(예: 진단 라벨) 공유 파일에 나가지 않게 키 집합을 고정한다."""
        data = json.loads((self.out / "case-001" / "candidate.json").read_text(encoding="utf-8"))
        self.assertEqual(set(data), {"schema", "case_code", "plane", "plane_evidence", "purpose", "dataset", "orientation", "window", "candidate",
                                     "slices", "fingerprint", "status"})
        self.assertEqual(set(data["candidate"]), {"representative_slice_no", "representative_source_z", "selection", "box", "box_pixels", "image_width",
                                                   "image_height", "image_sha256"})
        self.assertEqual(set(data["slices"][0]), {"index", "source_z", "sha256", "width", "height", "file"})
        self.assertEqual(set(data["dataset"]), {"name", "record", "source", "paper", "license", "transform_note"})
        self.assertEqual(set(data["window"]), {"low_percentile", "high_percentile", "lo", "hi", "scope"})

    def test_no_private_provenance_is_created_by_default(self):
        """Codex 20차 CANDIDATE-020-05: 공유 출력 폴더에는 환자 연결 정보를 기본으로 만들지 않는다."""
        self.assertIsNone(self.report["private"])
        names = [p.name for p in self.out.rglob("*")]
        self.assertFalse([n for n in names if "PRIVATE" in n.upper() or "provenance" in n.lower()], names)
        for path in self.out.rglob("*"):
            if path.is_file():
                raw = path.read_bytes()
                for secret in (b"111111", b"111111-7", b".pck", b"acl_diagnosis", b"aclDiagnosis", b"kneeLR"):
                    self.assertNotIn(secret, raw, f"{path.name}: {secret!r}")
        self.assertIn("환자 연결 정보", (self.out / "README.txt").read_text(encoding="utf-8"))

    def test_private_provenance_only_goes_to_an_explicit_folder_outside_the_shared_one(self):
        private_dir = Path(self.tmp.name) / "restricted_private"
        out2 = Path(self.tmp.name) / "out2"
        report = prep.write_cases([self.case], out2, private_dir=private_dir)
        private = json.loads((private_dir / "PRIVATE_provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(report["private"], str(private_dir / "PRIVATE_provenance.json"))
        self.assertIn("공유 금지", private["WARNING"])
        self.assertEqual(private["cases"][0]["exam_id"], 111111)
        self.assertEqual(private["cases"][0]["acl_diagnosis_label"], 2)
        self.assertFalse(any("PRIVATE" in p.name.upper() for p in out2.rglob("*")))     # 공유 폴더 전체(재귀)에는 없다
        self.assertFalse((out2 / "PRIVATE_provenance.json").exists())

    def test_private_folder_inside_same_as_or_above_the_shared_folder_is_refused(self):
        base = Path(self.tmp.name)
        for name, out, private in (
            ("inside", base / "o_inside", base / "o_inside" / "private"),
            ("same", base / "o_same", base / "o_same"),
            ("parent", base / "parent" / "o_child", base / "parent"),
            ("dotdot", base / "o_dotdot", base / "o_dotdot" / "x" / ".."),
        ):
            with self.subTest(case=name):
                with self.assertRaises(prep.CandidateError):
                    prep.write_cases([self.case], out, private_dir=private)
                self.assertFalse(out.exists(), "거절되면 공유 폴더도 만들지 않는다")
        occupied = base / "occupied_private"
        occupied.mkdir()
        (occupied / "something.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(prep.CandidateError):
            prep.write_cases([self.case], base / "o_occ", private_dir=occupied)

    def test_review_sheet_is_offline_escaped_and_has_the_box(self):
        sheet = (self.out / "review_sheet.html").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"(?:src|href)=[\"']https?://", sheet))      # 외부 주소 없음
        self.assertNotIn("111111", sheet)
        self.assertNotIn("PRIVATE", sheet)
        for text in ("ACL이 포함된 참고 영역 표시 연습", "임상 검증 아님", "후보", "승인되지 않았습니다", "CC BY-NC-ND 4.0", "Clinical Hospital Centre Rijeka"):
            self.assertIn(text, sheet)
        box = self.case.candidate["box"]
        self.assertIn(f'x="{box["x"]}" y="{box["y"]}" width="{box["width"]}" height="{box["height"]}"', sheet)
        self.assertEqual(sheet.count("data-check="), len(prep.CHECKLIST))
        self.assertIn(self.case.candidate["representative_slice_no"] + 1, [6 + 1])
        self.assertIn("slice_006.png", sheet)

    def test_review_sheet_escapes_unsafe_text(self):
        evil = prep.Case(case_code="case-001", slices=self.case.slices, candidate=self.case.candidate, window=self.case.window, private={})
        sheet = prep.render_review_sheet([evil])
        self.assertNotIn("<script>alert", sheet)


class SummarizeTests(unittest.TestCase):
    def setUp(self):
        self.case = prep.build_case(synthetic_volume(), meta_row(), "case-001", count=12)
        self.meta = [prep.shareable(self.case)]
        self.all_checks = {key: True for key, _ in prep.CHECKLIST}

    def review(self, reviewer, decision="approve", **over):
        data = {"schema": prep.REVIEW_SCHEMA, "reviewer": reviewer, "case_code": "case-001", "fingerprint": prep.fingerprint(self.case),
                "decision": decision, "checks": dict(self.all_checks), "note": ""}
        data.update(over)
        return data

    def test_three_approvals_are_ready_but_not_an_approval(self):
        report = prep.summarize_reviews(self.meta, [self.review(f"reviewer-00{i}") for i in (1, 2, 3)])
        self.assertEqual(report["cases"]["case-001"]["status"], "all_approved")
        self.assertEqual(report["errors"], [])
        self.assertIn("승인이 아닙니다", report["note"])
        self.assertNotIn("APPROVED_SLIDE_REFERENCES", json.dumps(report))
        self.assertNotIn("reviewed_by", json.dumps(report))                      # 승인 항목을 만들지 않는다

    def test_one_rejection_or_too_few_reviewers(self):
        reject = self.review("reviewer-003", "reject", checks={**self.all_checks, "acl_region": False})
        report = prep.summarize_reviews(self.meta, [self.review("reviewer-001"), self.review("reviewer-002"), reject])
        self.assertEqual(report["cases"]["case-001"]["status"], "rejected")
        two = prep.summarize_reviews(self.meta, [self.review("reviewer-001"), self.review("reviewer-002")])
        self.assertEqual(two["cases"]["case-001"]["status"], "incomplete")
        none = prep.summarize_reviews(self.meta, [])
        self.assertEqual(none["cases"]["case-001"]["status"], "incomplete")

    def test_invalid_reviews_are_reported_and_ignored(self):
        good = [self.review(f"reviewer-00{i}") for i in (1, 2)]
        bad = [
            self.review("reviewer-003", fingerprint="0" * 64),                      # 다른 후보에 대한 검수
            self.review("Kim Minsu"),                                                # 실명 형식
            self.review("reviewer-003", case_code="case-999"),
            self.review("reviewer-003", decision="maybe"),
            self.review("reviewer-003", checks={"opens": True}),
            self.review("reviewer-003", checks={**self.all_checks, "opens": "yes"}),
            self.review("reviewer-003", checks={**self.all_checks, "orientation": False}),   # 승인인데 체크 누락
            self.review("reviewer-001"),                                             # 같은 검수자 중복
            {"schema": "other"}, "text", None,
        ]
        report = prep.summarize_reviews(self.meta, good + bad)
        self.assertEqual(report["cases"]["case-001"]["status"], "incomplete")      # 유효한 것은 2명뿐
        self.assertEqual(len(report["errors"]), len(bad))
        self.assertEqual(report["cases"]["case-001"]["reviewers"], ["reviewer-001", "reviewer-002"])

    def test_three_different_reviewers_needed_not_one_repeated(self):
        report = prep.summarize_reviews(self.meta, [self.review("reviewer-001")] * 3)
        self.assertEqual(report["cases"]["case-001"]["status"], "incomplete")


class NoAutoApprovalAndIsolationTests(unittest.TestCase):
    def test_module_never_touches_the_approval_tables(self):
        source = (ROOT / "class" / "knee" / "candidate_prep.py").read_text(encoding="utf-8")
        for forbidden in ("APPROVED_SLIDE_REFERENCES[", "APPROVED_REFERENCES[", "APPROVED_SLIDE_REFERENCES.update", ".setdefault(", "KNEE_ROI_GRADE_ENABLED",
                          "sqlalchemy", "requests", "urllib", "boto", "upload_bytes", "nasServices"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_server_code_does_not_import_the_tool(self):
        for folder in ("routers", "services", "repositories", "schemas", "utils"):
            for path in (ROOT / folder).glob("*.py"):
                self.assertNotIn("candidate_prep", path.read_text(encoding="utf-8", errors="replace"), str(path))
        self.assertNotIn("candidate_prep", (ROOT / "main.py").read_text(encoding="utf-8", errors="replace"))
        for path in (ROOT / "class" / "knee").glob("*.py"):
            if path.name != "candidate_prep.py":
                self.assertNotIn("candidate_prep", path.read_text(encoding="utf-8", errors="replace"), path.name)

    def test_no_new_top_level_folder_for_tools(self):
        self.assertFalse((ROOT / "tools").exists())

    def test_running_the_tool_leaves_the_empty_approval_tables_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            prep.write_cases([prep.build_case(synthetic_volume(), meta_row(), "case-001", count=12)], Path(tmp) / "o")
        data = import_module("class.knee.slide_references_data").REFERENCE_ROWS   # 도구는 승인표를 바꾸지 않는다(데이터 파일 행 그대로)
        self.assertEqual(sorted((r.study_idx, r.reference_id) for rows in slide_refs.APPROVED_SLIDE_REFERENCES.values() for r in rows), sorted((r["study_idx"], r["reference_id"]) for r in data))
        self.assertEqual(import_module("class.knee.reference_rois").APPROVED_REFERENCES, {})


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "vol").mkdir()
        (self.dir / "vol" / "111111-7.pck").write_bytes(pickle.dumps(synthetic_volume()))
        (self.dir / "metadata.csv").write_text(
            "examId,seriesNo,aclDiagnosis,kneeLR,roiX,roiY,roiZ,roiHeight,roiWidth,roiDepth,volumeFilename\n"
            f"111111,7,2,1,{BOX['x']},{BOX['y']},4,{BOX['height']},{BOX['width']},4,111111-7.pck\n", encoding="utf-8")

    def run_cli(self, *args):
        code = ("import importlib,sys; sys.exit(importlib.import_module('class.knee.candidate_prep').main(sys.argv[1:]))")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}   # 한글 안내를 콘솔 인코딩과 무관하게 받는다
        return subprocess.run([sys.executable, "-B", "-c", code, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env)

    def prepare_args(self, out, *extra):
        return ["prepare", "--metadata", str(self.dir / "metadata.csv"), "--volumes", str(self.dir / "vol"), "--volume-file", "111111-7.pck",
                "--out", str(out), "--slices", "12", *extra]

    def test_refuses_without_the_trust_flag(self):
        done = self.run_cli(*self.prepare_args(self.dir / "out"))
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("신뢰하는 로컬 자료", done.stderr)
        self.assertFalse((self.dir / "out").exists())

    def test_prepare_then_summarize_end_to_end(self):
        out = self.dir / "out"
        done = self.run_cli(*self.prepare_args(out, "--trusted-local"))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("CANDIDATE_ONLY_NOT_APPROVED", done.stdout)
        candidate = json.loads((out / "case-001" / "candidate.json").read_text(encoding="utf-8"))
        reviews = self.dir / "reviews"
        reviews.mkdir()
        for i in (1, 2, 3):
            (reviews / f"review_reviewer-00{i}_case-001.json").write_text(json.dumps({
                "schema": prep.REVIEW_SCHEMA, "reviewer": f"reviewer-00{i}", "case_code": "case-001", "fingerprint": candidate["fingerprint"],
                "decision": "approve", "checks": {key: True for key, _ in prep.CHECKLIST}, "note": ""}), encoding="utf-8")
        summary = self.run_cli("summarize", "--out", str(out), "--reviews", str(reviews))
        self.assertEqual(summary.returncode, 0, summary.stderr)
        self.assertEqual(json.loads(summary.stdout)["cases"]["case-001"]["status"], "all_approved")

    def test_private_dir_option_defaults_to_none_and_must_be_outside(self):
        out = self.dir / "out_default"
        done = self.run_cli(*self.prepare_args(out, "--trusted-local"))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIsNone(json.loads(done.stdout)["private"])
        self.assertFalse(any("PRIVATE" in p.name.upper() for p in out.rglob("*")))
        private = self.dir / "restricted"
        out2 = self.dir / "out_private"
        done2 = self.run_cli(*self.prepare_args(out2, "--trusted-local", "--private-dir", str(private)))
        self.assertEqual(done2.returncode, 0, done2.stderr)
        self.assertTrue((private / "PRIVATE_provenance.json").exists())
        self.assertFalse(any("PRIVATE" in p.name.upper() for p in out2.rglob("*")))
        out3 = self.dir / "out_inside"
        done3 = self.run_cli(*self.prepare_args(out3, "--trusted-local", "--private-dir", str(out3 / "private")))
        self.assertNotEqual(done3.returncode, 0)
        self.assertIn("바깥", done3.stderr)
        self.assertFalse(out3.exists())

    def test_unknown_volume_file_is_refused(self):
        args = self.prepare_args(self.dir / "out", "--trusted-local")
        args[args.index("111111-7.pck")] = "999999-9.pck"
        done = self.run_cli(*args)
        self.assertNotEqual(done.returncode, 0)
        self.assertFalse((self.dir / "out").exists())


if __name__ == "__main__":
    unittest.main()
