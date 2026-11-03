"""
무릎 슬라이드 문제 "대표 슬라이스" 채점 골격 테스트 (비활성 골격, 합성 데이터만).

설계: designs/MSN-20260930-001/REPRESENTATIVE_SLICE_CONTRACT.md. 인수 조건 AC-01~05 중 Claude 담당 부분.
- 경로·해시·박스·토큰 키는 모두 가짜 값이다. 실제 KneeMRI 영상·실제 승인·실제 NAS·실제 DB 는 쓰지 않는다.
- 승인표(APPROVED_SLIDE_REFERENCES)는 시험 동안에만 임시로 채우고 끝나면 반드시 비운다(기본은 빈 표).
실행: python -m pytest tests/test_knee_slice.py   (torch 불필요)
"""
import dataclasses
import hashlib
import json
import os
import random
import sys
import time
import unittest
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from db import get_db
from routers import kneeLearning
from services import kneeRoiGradeServices as knee_grade
from services import kneeSliceGradeServices as slice_grade

manifest_mod = import_module("class.knee.slice_manifest")
slide_refs = import_module("class.knee.slide_references")
tokens = import_module("class.knee.problem_token")
knee_config = import_module("class.knee.config")

KEY = "synthetic-test-key-0123456789abcdef0123456789"   # 가짜 서명 키(32자 이상)
ROOT = "/synthetic/"
NOW = 1_800_000_000


def make_paths(n=24):
    return [f"/synthetic/knee/{i:03d}.png" for i in range(n)]


def st_image(paths=None, **dumps):
    return json.dumps(paths if paths is not None else make_paths(), **dumps)


def fake_sha(i):
    return hashlib.sha256(f"synthetic-image-{i}".encode()).hexdigest()


def manifest_of(paths=None):
    return manifest_mod.build_manifest(st_image(paths), (ROOT,))


def make_ref(manifest, slice_no=12, study_idx=3, **over):
    fields = dict(
        reference_id=7, study_idx=study_idx, manifest_version=manifest.version, slice_set_sha256=manifest.list_sha256,
        slice_no=slice_no, image_path=manifest.paths[slice_no], image_sha256=fake_sha(slice_no), plane="sagittal",
        x=0.3, y=0.3, width=0.4, height=0.4, source="synthetic dataset record v0", version="v1",
        reviewed_by="reviewer-000", reviewed_at="2026-10-12T00:00:00",
    )
    fields.update(over)
    return slide_refs.SlideReference(**fields)


def issue(manifest, ref, user_idx=5, study_idx=3, now=NOW, ttl=7200, **over):
    args = dict(
        key=KEY.encode(), now=now, ttl_s=ttl, user_idx=user_idx, study_idx=study_idx, manifest_version=manifest.version,
        slice_no=ref.slice_no, slice_set_sha256=manifest.list_sha256, image_sha256=ref.image_sha256,
        reference_id=ref.reference_id, reference_version=ref.version,
    )
    args.update(over)
    return tokens.issue_token(**args)


def box_mask(x=0.3, y=0.3, width=0.4, height=0.4):
    return knee_grade.user_mask(roi_type="box", x=x, y=y, width=width, height=height)


class SyntheticBase(unittest.TestCase):
    """환경 변수(자산 루트·가짜 키)와 승인표를 시험 동안만 채우고, 실제 .env 값은 보지 않는다."""

    def setUp(self):
        env = mock.patch.dict(os.environ, {"KNEE_ASSET_ROOTS": ROOT, "KNEE_PROBLEM_TOKEN_KEY": KEY, "KNEE_PROBLEM_TOKEN_TTL_S": "7200"})
        env.start()
        self.addCleanup(env.stop)
        dotenv = mock.patch.object(knee_config, "ENV_PATH", Path(os.devnull) / "없음.env")
        dotenv.start()
        self.addCleanup(dotenv.stop)
        table = mock.patch.dict(slide_refs.APPROVED_SLIDE_REFERENCES, {}, clear=True)
        table.start()
        self.addCleanup(table.stop)

    def approve(self, ref):
        slide_refs.APPROVED_SLIDE_REFERENCES.setdefault(ref.study_idx, []).append(ref)


# ── AC-01: 목록·후보 z·번호 매핑 ───────────────────────────────────────────────
class ManifestTests(unittest.TestCase):
    def test_list_hash_is_sha256_of_compact_ordered_json(self):
        paths = make_paths(3)
        expected = hashlib.sha256(json.dumps(paths, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        self.assertEqual(manifest_mod.list_hash(paths), expected)
        self.assertEqual(manifest_mod.build_manifest(st_image(paths), (ROOT,)).list_sha256, expected)

    def test_whitespace_only_differences_keep_the_same_hash(self):
        paths = make_paths(4)
        a = manifest_mod.build_manifest(st_image(paths), (ROOT,))
        b = manifest_mod.build_manifest(st_image(paths, indent=4), (ROOT,))
        c = manifest_mod.build_manifest("  " + st_image(paths, separators=(" , ", " : ")) + "\n", (ROOT,))
        self.assertEqual(a.list_sha256, b.list_sha256)
        self.assertEqual(a.list_sha256, c.list_sha256)
        self.assertEqual(a.version, f"ordered-paths-json-v1:{a.list_sha256}")

    def test_reorder_insert_delete_or_path_change_changes_the_hash(self):
        paths = make_paths(5)
        base = manifest_mod.list_hash(paths)
        variants = [
            paths[::-1],                                  # 재정렬
            paths[:2] + ["/synthetic/knee/new.png"] + paths[2:],   # 삽입
            paths[:-1],                                   # 삭제
            paths[:1] + ["/synthetic/knee/changed.png"] + paths[2:],   # 경로 변경
            [p.replace("0", "O") if i == 0 else p for i, p in enumerate(paths)],   # 글자 하나 변경
        ]
        for variant in variants:
            with self.subTest(variant=variant[:2]):
                self.assertNotEqual(manifest_mod.list_hash(variant), base)

    def test_paths_keep_their_registered_order_and_spelling(self):
        paths = ["/synthetic/Z/B.png", "/synthetic/a/a.PNG", "/synthetic/m/한글.png"]
        manifest = manifest_mod.build_manifest(st_image(paths), (ROOT,))
        self.assertEqual(list(manifest.paths), paths)            # 정렬·소문자·슬래시 치환 없음
        self.assertEqual(manifest.count, 3)

    def test_unusable_lists_are_rejected(self):
        ok = make_paths(2)
        bad = {
            "not json": "[not json",
            "object": '{"a": 1}',
            "empty": "[]",
            "null": "null",
            "nested": json.dumps([ok, ok]),
            "null item": json.dumps([ok[0], None]),
            "number item": json.dumps([ok[0], 3]),
            "empty path": json.dumps([ok[0], ""]),
            "duplicate": json.dumps([ok[0], ok[0]]),
            "outside root": json.dumps(["/other/a.png"]),
            "root prefix trick": json.dumps(["/synthetic2/a.png"]),
            "root itself": json.dumps([ROOT]),
            "parent segment": json.dumps(["/synthetic/../etc/a.png"]),
            "dot segment": json.dumps(["/synthetic/./a.png"]),
            "backslash": json.dumps(["/synthetic/a\\b.png"]),
            "url scheme": json.dumps(["/synthetic/http://x/a.png"]),
            "control char": json.dumps(["/synthetic/a\u0007.png"]),
            "leading space": json.dumps([" /synthetic/a.png"]),
            "trailing space": json.dumps(["/synthetic/a.png "]),
            "too many": json.dumps([f"/synthetic/{i}.png" for i in range(manifest_mod.MAX_SLICES + 1)]),
            "too long": json.dumps(["/synthetic/" + "a" * manifest_mod.MAX_PATH_LENGTH]),
        }
        for name, text in bad.items():
            with self.subTest(case=name):
                with self.assertRaises(manifest_mod.SliceManifestError):
                    manifest_mod.build_manifest(text, (ROOT,))
        for value in (None, 5, ["a"], b"[]"):
            with self.subTest(non_string=value):
                with self.assertRaises(manifest_mod.SliceManifestError):
                    manifest_mod.build_manifest(value, (ROOT,))

    def test_no_configured_root_means_no_path_is_allowed(self):
        with self.assertRaises(manifest_mod.SliceManifestError):
            manifest_mod.build_manifest(st_image(), ())

    def test_max_size_list_is_accepted(self):
        paths = [f"/synthetic/{i}.png" for i in range(manifest_mod.MAX_SLICES)]
        self.assertEqual(manifest_mod.build_manifest(st_image(paths), (ROOT,)).count, manifest_mod.MAX_SLICES)


class SliceIndexParserTests(unittest.TestCase):
    def test_accepts_plain_unsigned_integers(self):
        for raw, expected in (("0", 0), ("7", 7), ("12", 12), ("999999", 999999)):
            self.assertEqual(manifest_mod.parse_slice_index(raw), expected)
        self.assertEqual(manifest_mod.parse_slice_index("23", 24), 23)

    def test_rejects_missing_and_malformed_values(self):
        for raw in (None, "", "-1", "+1", "1.5", "1.0", "01", "00", "1e2", " 1", "1 ", "true", "True", "x", "０", "١", "1_0", "0x1", "1234567"):
            with self.subTest(raw=raw):
                with self.assertRaises(manifest_mod.SliceIndexError):
                    manifest_mod.parse_slice_index(raw)
        for raw in (1, 1.0, True, False, b"1", ["1"]):                  # 문자열이 아닌 값
            with self.subTest(non_string=raw):
                with self.assertRaises(manifest_mod.SliceIndexError):
                    manifest_mod.parse_slice_index(raw)

    def test_rejects_out_of_range(self):
        for raw in ("24", "25", "100"):
            with self.assertRaises(manifest_mod.SliceIndexError):
                manifest_mod.parse_slice_index(raw, 24)

    def test_display_number_is_one_based(self):
        self.assertEqual(manifest_mod.display_slice_number(0), 1)
        self.assertEqual(manifest_mod.display_slice_number(12), 13)


class CandidateTests(unittest.TestCase):
    def test_middle_candidate_for_odd_and_even_depth(self):
        self.assertEqual(manifest_mod.candidate_source_z(10, 5, 24), 12)    # 10..14 의 가운데
        self.assertEqual(manifest_mod.candidate_source_z(10, 4, 24), 12)    # 짝수 깊이는 뒤쪽 가운데(10,11,[12],13)
        self.assertEqual(manifest_mod.candidate_source_z(10, 1, 24), 10)
        self.assertEqual(manifest_mod.candidate_source_z(0, 2, 2), 1)
        self.assertEqual(manifest_mod.candidate_source_z(20, 4, 24), 22)    # 범위 끝에 딱 맞음

    def test_roi_z_is_the_range_start_not_the_representative(self):
        self.assertNotEqual(manifest_mod.candidate_source_z(10, 5, 24), 10)

    def test_invalid_ranges_are_rejected_not_clipped(self):
        cases = [(-1, 4, 24), (0, 0, 24), (0, -2, 24), (21, 4, 24), (24, 1, 24), (5, 4, 0), (5, 4, -1)]
        for args in cases:
            with self.subTest(args=args):
                with self.assertRaises(manifest_mod.SliceManifestError):
                    manifest_mod.candidate_source_z(*args)

    def test_non_integer_inputs_are_rejected(self):
        for args in ((True, 4, 24), (10, True, 24), (10, 4, True), (10.0, 4, 24), (10, 4.5, 24), ("10", 4, 24), (None, 4, 24)):
            with self.subTest(args=args):
                with self.assertRaises(manifest_mod.SliceManifestError):
                    manifest_mod.candidate_source_z(*args)


class SourceMappingTests(unittest.TestCase):
    def test_identity_mapping(self):
        self.assertEqual(manifest_mod.source_z_to_index(12, range(24)), 12)

    def test_partial_extraction_maps_to_a_different_index(self):
        numbers = [8, 9, 10, 11, 12, 13]            # 원본 8~13 만 등록
        self.assertEqual(manifest_mod.source_z_to_index(12, numbers), 4)

    def test_reordered_and_sampled_lists(self):
        numbers = [20, 10, 14, 12, 0]               # 순서 바뀜 + 일부 표본
        self.assertEqual(manifest_mod.source_z_to_index(12, numbers), 3)
        self.assertEqual(manifest_mod.source_z_to_index(0, numbers), 4)

    def test_missing_duplicate_or_bad_tables_are_rejected(self):
        for z, numbers in ((12, [1, 2, 3]), (12, [12, 12, 3]), (True, [1, True]), ("12", [12]), (12, [12, "3"]), (12, [12.0])):
            with self.subTest(z=z, numbers=numbers):
                with self.assertRaises(manifest_mod.SliceManifestError):
                    manifest_mod.source_z_to_index(z, numbers)


# ── AC-02: 승인 해석 ───────────────────────────────────────────────────────────
class ResolveTests(SyntheticBase):
    def setUp(self):
        super().setUp()
        self.manifest = manifest_of()

    def test_empty_table_has_no_reference(self):
        res = slide_refs.resolve(3, self.manifest)
        self.assertEqual((res.status, res.reference), ("none", None))

    def test_exactly_one_valid_approval_is_ok(self):
        ref = make_ref(self.manifest)
        self.approve(ref)
        res = slide_refs.resolve(3, self.manifest)
        self.assertEqual((res.status, res.reference), ("ok", ref))

    def test_two_active_approvals_are_ambiguous_and_none_is_picked(self):
        self.approve(make_ref(self.manifest, 12, reference_id=7))
        self.approve(make_ref(self.manifest, 5, reference_id=8))     # 다른 장에 여러 개
        res = slide_refs.resolve(3, self.manifest)
        self.assertEqual((res.status, res.reference), ("ambiguous", None))

    def test_two_identical_looking_approvals_are_ambiguous_too(self):
        self.approve(make_ref(self.manifest, 12, reference_id=7))
        self.approve(make_ref(self.manifest, 12, reference_id=9, version="v2"))
        self.assertEqual(slide_refs.resolve(3, self.manifest).status, "ambiguous")

    def test_revoked_rows_are_ignored(self):
        self.approve(make_ref(self.manifest, 12, reference_id=7, status="R"))
        self.assertEqual(slide_refs.resolve(3, self.manifest).status, "none")
        self.approve(make_ref(self.manifest, 12, reference_id=8))
        res = slide_refs.resolve(3, self.manifest)
        self.assertEqual((res.status, res.reference.reference_id), ("ok", 8))

    def test_each_mismatch_means_no_reference_without_substitution(self):
        other = manifest_of(make_paths(24)[::-1])                     # 같은 경로, 순서만 반대인 다른 목록
        changed = {
            "wrong study": dict(study_idx=4),
            "wrong path": dict(image_path="/synthetic/knee/other.png"),
            "path of another slice": dict(image_path=self.manifest.paths[3]),
            "other list hash": dict(slice_set_sha256=other.list_sha256),
            "other manifest version": dict(manifest_version=other.version),
            "slice_no out of range": dict(slice_no=24),
            "slice_no negative": dict(slice_no=-1),
            "slice_no bool": dict(slice_no=True),
            "image hash upper": dict(image_sha256=fake_sha(12).upper()),
            "image hash short": dict(image_sha256="abc"),
            "image hash empty": dict(image_sha256=""),
            "bad plane": dict(plane="oblique"),
            "bad coord": dict(coord_system="pixels"),
            "blank reviewer": dict(reviewed_by=" "),
            "blank source": dict(source=""),
            "blank version": dict(version=""),
            "blank reviewed_at": dict(reviewed_at=""),
            "zero width": dict(width=0.0),
            "box outside": dict(x=0.8, width=0.4),
            "nan box": dict(x=float("nan")),
            "bool box": dict(height=True),
        }
        for name, over in changed.items():
            with self.subTest(case=name):
                slide_refs.APPROVED_SLIDE_REFERENCES.clear()
                ref = dataclasses.replace(make_ref(self.manifest, 12), **over)   # 올바른 승인에서 한 곳만 어긋나게 만든다
                slide_refs.APPROVED_SLIDE_REFERENCES[3] = [ref]                    # 3번 문제의 승인 목록에 잘못된 행이 들어 있는 상황
                res = slide_refs.resolve(3, self.manifest)
                self.assertEqual((res.status, res.reference), ("none", None))
                self.assertTrue(res.detail.startswith("stale:"), res.detail)

    def test_old_approval_is_not_applied_to_a_changed_list(self):
        self.approve(make_ref(self.manifest))
        after_insert = manifest_of(make_paths(24)[:5] + ["/synthetic/knee/new.png"] + make_paths(24)[5:])
        self.assertEqual(slide_refs.resolve(3, after_insert).status, "none")
        reordered = manifest_of(make_paths(24)[::-1])
        self.assertEqual(slide_refs.resolve(3, reordered).status, "none")

    def test_other_study_rows_do_not_leak(self):
        self.approve(make_ref(self.manifest, study_idx=4))
        self.assertEqual(slide_refs.resolve(3, self.manifest).status, "none")


class ApprovalCandidateTests(unittest.TestCase):
    def args(self, **over):
        base = dict(manifest=manifest_of(), source_numbers=list(range(24)), roi_x=96, roi_y=64, roi_z=10, roi_width=128, roi_height=160,
                    roi_depth=5, volume_slices=24, image_width=320, image_height=320)
        base.update(over)
        return base

    def test_candidate_is_computed_from_the_dataset_roi(self):
        cand = slide_refs.candidate_from_dataset_roi(**self.args())
        self.assertEqual((cand["slice_no"], cand["source_slice_no"]), (12, 12))
        self.assertEqual(cand["image_path"], make_paths()[12])
        self.assertEqual((cand["x"], cand["y"], cand["width"], cand["height"]), (0.3, 0.2, 0.4, 0.5))   # 왼쪽 위 기준 정규화
        self.assertEqual(cand["slice_set_sha256"], manifest_of().list_sha256)

    def test_mapping_is_applied_when_numbers_differ(self):
        # 원본 볼륨은 48장이고 그 가운데 8~31번 24장만 등록한 경우: 원본 z 12 는 등록 배열의 4번째(0부터)
        numbers = list(range(8, 32))
        cand = slide_refs.candidate_from_dataset_roi(**self.args(source_numbers=numbers, volume_slices=48))
        self.assertEqual((cand["slice_no"], cand["source_slice_no"]), (4, 12))

    def test_sampled_registration_maps_through_the_table(self):
        # 48장 볼륨에서 짝수 번호만 24장 등록: 후보 z = 20 + 5//2 = 22 → 등록 배열 11번째
        numbers = [2 * i for i in range(24)]
        cand = slide_refs.candidate_from_dataset_roi(**self.args(source_numbers=numbers, volume_slices=48, roi_z=20, roi_depth=5))
        self.assertEqual((cand["slice_no"], cand["source_slice_no"]), (11, 22))

    def test_source_number_table_must_match_the_list_and_the_volume(self):
        """번호 표 전체를 검사한다(015-01): 길이 = 목록 장 수, 모두 정수(bool 불가)·유일·0 이상 볼륨 장 수 미만.
        표에 후보 z 가 들어 있다는 것만으로 통과시키지 않는다."""
        base = list(range(24))
        bad = {
            "one entry only": [12],
            "longer than the list": list(range(25)),
            "shorter than the list": list(range(23)),
            "negative": [-1] + list(range(1, 24)),
            "beyond the volume": list(range(100, 124)),
            "one beyond the volume": base[:-1] + [24],
            "bool": [True] + list(range(1, 24)),
            "duplicate": [0, 0] + list(range(2, 24)),
            "float": [float(n) for n in base],
            "string": [str(n) for n in base],
            "none": base[:-1] + [None],
            "not a list": 12,
        }
        for name, numbers in bad.items():
            with self.subTest(case=name):
                with self.assertRaises(manifest_mod.SliceManifestError):
                    slide_refs.candidate_from_dataset_roi(**self.args(source_numbers=numbers))
        # 기준선: 같은 호출에서 올바른 표는 통과한다
        self.assertEqual(slide_refs.candidate_from_dataset_roi(**self.args(source_numbers=base))["slice_no"], 12)
        self.assertEqual(slide_refs.candidate_from_dataset_roi(**self.args(source_numbers=iter(base)))["slice_no"], 12)

    def test_the_table_is_checked_even_when_the_candidate_is_not_in_it(self):
        with self.assertRaises(manifest_mod.SliceManifestError):
            slide_refs.candidate_from_dataset_roi(**self.args(source_numbers=[1, 2, 3]))

    def test_invalid_candidates_are_rejected_not_fixed(self):
        bad = [
            dict(roi_x=-1), dict(roi_y=-1), dict(roi_width=0), dict(roi_height=0),
            dict(roi_x=250, roi_width=128),                  # 영상 밖으로 나감(자르지 않는다)
            dict(roi_y=300, roi_height=100),
            dict(image_width=0), dict(image_height=-5),
            dict(roi_z=22, roi_depth=5),                    # z 범위가 볼륨을 넘음
            dict(source_numbers=[1, 2, 3]),                  # 대표 후보가 등록된 장에 없음
            dict(roi_x=96.0), dict(roi_z=True),
        ]
        for over in bad:
            with self.subTest(over=over):
                with self.assertRaises(manifest_mod.SliceManifestError):
                    slide_refs.candidate_from_dataset_roi(**self.args(**over))

    def test_candidate_beyond_the_list_is_rejected(self):
        numbers = list(range(30))                           # 번호 표는 30개인데 목록은 24장
        with self.assertRaises(manifest_mod.SliceManifestError):
            slide_refs.candidate_from_dataset_roi(**self.args(source_numbers=numbers, roi_z=26, roi_depth=2, volume_slices=30))


# ── AC-04 중 순수 부분: problem_token ──────────────────────────────────────────
class TokenTests(unittest.TestCase):
    def setUp(self):
        self.manifest = manifest_of()
        self.ref = make_ref(self.manifest)
        self.token = issue(self.manifest, self.ref)

    _DEFAULT = object()

    def verify(self, token=_DEFAULT, **over):
        args = dict(key=KEY.encode(), now=NOW + 10, user_idx=5, study_idx=3)
        args.update(over)
        return tokens.verify_token(self.token if token is self._DEFAULT else token, **args)

    def test_round_trip_and_payload_has_no_secrets(self):
        claims = self.verify()
        self.assertEqual((claims["u"], claims["s"], claims["sn"], claims["rv"]), (5, 3, 12, "v1"))
        self.assertEqual(set(claims), {"v", "u", "s", "mv", "sn", "ls", "is", "ri", "rv", "iat", "exp", "jti"})
        self.assertEqual(claims["exp"] - claims["iat"], 7200)
        text = json.dumps(claims)
        for forbidden in ("x", "y", "width", "height", "diagnosis", "source", "reviewed", "path", "nas", "email", "name"):
            self.assertNotIn(f'"{forbidden}"', text)
        self.assertNotIn("/synthetic/", text)

    def test_reuse_before_expiry_is_allowed(self):
        self.assertEqual(self.verify()["jti"], self.verify(now=NOW + 7000)["jti"])

    def test_expiry_boundary(self):
        self.verify(now=NOW + 7199)
        with self.assertRaises(tokens.TokenExpired):
            self.verify(now=NOW + 7200)
        with self.assertRaises(tokens.TokenExpired):
            self.verify(now=NOW + 99999)

    def test_tampering_is_rejected(self):
        head, body, sig = self.token.split(".")
        flipped_sig = sig[:-2] + ("A" if sig[-2] != "A" else "B") + sig[-1]
        forged_body = body[:-2] + ("A" if body[-2] != "A" else "B") + body[-1]
        for name, token in {
            "signature": f"{head}.{body}.{flipped_sig}", "body": f"{head}.{forged_body}.{sig}", "missing part": f"{head}.{body}",
            "extra part": f"{self.token}.x", "other prefix": f"zz1.{body}.{sig}", "empty": "", "dots": "..", "non ascii": self.token + "한",
            "huge": "a" * 5000, "none": None, "bytes": self.token.encode(), "number": 12345,
        }.items():
            with self.subTest(case=name):
                with self.assertRaises((tokens.TokenInvalid, tokens.TokenExpired)):
                    self.verify(token)
                with self.assertRaises(tokens.TokenInvalid):
                    self.verify(token)

    def signed(self, payload):
        """올바른 키로 서명했지만 내용이 비정상인 토큰을 만든다(서명만 믿으면 통과해 버리는 경우를 시험한다)."""
        body = tokens._b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        return f"kt1.{body}.{tokens._sign(KEY.encode(), body)}"

    def test_validly_signed_but_malformed_payloads_are_rejected(self):
        good = self.verify()
        self.assertEqual(self.verify(self.signed(good))["s"], 3)                       # 기준선: 같은 내용을 다시 서명하면 통과
        bad = {
            "extra field": {**good, "x": 0.3},
            "missing field": {k: v for k, v in good.items() if k != "jti"},
            "future version": {**good, "v": 2},
            "string user": {**good, "u": "5"},
            "bool user": {**good, "u": True},
            "float study": {**good, "s": 3.0},
            "string exp": {**good, "exp": "9999999999"},
            "list payload": [1, 2, 3],
            "string payload": "hello",
        }
        for name, payload in bad.items():
            with self.subTest(case=name):
                with self.assertRaises(tokens.TokenInvalid):
                    self.verify(self.signed(payload))

    def test_wrong_key_user_or_study_is_rejected(self):
        with self.assertRaises(tokens.TokenInvalid):
            self.verify(key=("other-key-" * 5).encode())
        with self.assertRaises(tokens.TokenInvalid):
            self.verify(user_idx=6)
        with self.assertRaises(tokens.TokenInvalid):
            self.verify(study_idx=4)

    def test_future_issue_time_is_rejected(self):
        with self.assertRaises(tokens.TokenInvalid):
            self.verify(now=NOW - 3600)

    def test_tokens_differ_between_issues_but_both_verify(self):
        other = issue(self.manifest, self.ref)
        self.assertNotEqual(self.token, other)
        self.assertEqual(self.verify(other)["u"], 5)

    def test_error_messages_do_not_contain_the_token(self):
        for token in (self.token[:-3] + "AAA", "garbage"):
            try:
                self.verify(token)
            except tokens.TokenInvalid as exc:
                self.assertNotIn(self.token, str(exc))
                self.assertNotIn(token, str(exc))


class TokenKeyConfigTests(unittest.TestCase):
    def setUp(self):
        dotenv = mock.patch.object(knee_config, "ENV_PATH", Path(os.devnull) / "없음.env")
        dotenv.start()
        self.addCleanup(dotenv.stop)

    def test_key_must_be_long_and_separate_from_the_session_key(self):
        with mock.patch.dict(os.environ, {"KNEE_PROBLEM_TOKEN_KEY": KEY, "SECRET_KEY": "different-session-secret"}):
            self.assertEqual(knee_config.problem_token_key(), KEY.encode())
        with mock.patch.dict(os.environ, {"KNEE_PROBLEM_TOKEN_KEY": "short"}):
            self.assertIsNone(knee_config.problem_token_key())
        with mock.patch.dict(os.environ, {"KNEE_PROBLEM_TOKEN_KEY": KEY, "SECRET_KEY": KEY}):
            self.assertIsNone(knee_config.problem_token_key())          # 세션 키와 같으면 쓰지 않는다
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KNEE_PROBLEM_TOKEN_KEY", None)
            self.assertIsNone(knee_config.problem_token_key())

    def test_ttl_default_and_bounds(self):
        with mock.patch.dict(os.environ, {"KNEE_PROBLEM_TOKEN_TTL_S": ""}):
            self.assertEqual(knee_config.problem_token_ttl_s(), 7200)
        for raw, expected in (("30", 60), ("86401", 86400), ("abc", 7200), ("900", 900), ("-5", 60)):
            with mock.patch.dict(os.environ, {"KNEE_PROBLEM_TOKEN_TTL_S": raw}):
                self.assertEqual(knee_config.problem_token_ttl_s(), expected, raw)

    def test_asset_roots_parsing_is_strict(self):
        for raw, expected in (("/a/", ("/a/",)), ("/a/, /b/c/", ("/a/", "/b/c/")), ("", ()), ("a/", ()), ("/a", ()), ("/", ()),
                              ("/a/../b/", ()), ("/ok/,bad", ("/ok/",))):
            with mock.patch.dict(os.environ, {"KNEE_ASSET_ROOTS": raw}):
                self.assertEqual(knee_config.asset_roots(), expected, raw)


# ── AC-02/04: 채점 판단(서비스 수준) ───────────────────────────────────────────────
class GradeSlideTests(SyntheticBase):
    def setUp(self):
        super().setUp()
        self.manifest = manifest_of()
        self.ref = make_ref(self.manifest)
        self.approve(self.ref)
        self.token = issue(self.manifest, self.ref)

    def run_slide(self, index="12", token="default", user_idx=5, study_idx=3, image=None, now=NOW + 5, mask=None, **extra):
        return slice_grade.grade_slide(
            user_mask=mask if mask is not None else box_mask(), user_idx=user_idx, study_idx=study_idx,
            st_image=image if image is not None else st_image(), slice_index_raw=index,
            problem_token=self.token if token == "default" else token, now=now, **extra)

    def assertNotGraded(self, outcome, reason):
        self.assertFalse(outcome.result["graded"])
        self.assertEqual(outcome.result["not_graded_reason"], reason)
        self.assertIsNone(outcome.image_path)
        for key in ("result", "iou", "overlap_px", "reference", "reference_bbox", "plane"):
            self.assertIsNone(outcome.result[key], key)

    def test_representative_slice_is_graded_with_the_server_path(self):
        out = self.run_slide()
        self.assertTrue(out.result["graded"])
        self.assertEqual(out.result["result"], "정답")
        self.assertEqual(out.result["slice_index"], 12)
        self.assertEqual(out.result["rule_version"], "D-069/2026-09-16")
        self.assertEqual(out.result["reference_basis"], "dataset_acl_region")
        self.assertIs(out.result["clinical_validated"], False)
        self.assertEqual(out.image_path, make_paths()[12])            # 배열 문자열이 아니라 서버의 단일 경로
        self.assertNotIn("reviewed_by", json.dumps(out.result))        # 검수자 코드는 응답에 없다

    def test_no_approval_means_no_reference_and_no_token_is_needed(self):
        slide_refs.APPROVED_SLIDE_REFERENCES.clear()
        for token in (None, "", "garbage", self.token):
            with self.subTest(token=token):
                self.assertNotGraded(self.run_slide(token=token), "no_reference_for_slice")
        with mock.patch.dict(os.environ, {"KNEE_PROBLEM_TOKEN_KEY": ""}):    # 키가 없어도 승인 없는 문제는 그대로 미채점
            self.assertNotGraded(self.run_slide(), "no_reference_for_slice")

    def test_ambiguous_approval_is_not_graded(self):
        self.approve(make_ref(self.manifest, 5, reference_id=8))
        self.assertNotGraded(self.run_slide(), "reference_ambiguous")

    def test_valid_non_representative_slice_is_not_graded_even_with_a_valid_token(self):
        for index in ("0", "11", "13", "23"):
            with self.subTest(index=index):
                self.assertNotGraded(self.run_slide(index=index), "no_reference_for_slice")

    def test_token_is_checked_before_the_non_representative_branch(self):
        self.assertNotGraded(self.run_slide(index="3", token=None), "token_invalid")
        self.assertNotGraded(self.run_slide(index="3", token="garbage"), "token_invalid")

    def test_token_failures(self):
        head, body, sig = self.token.split(".")
        self.assertNotGraded(self.run_slide(token=None), "token_invalid")
        changed_sig = sig[:-1] + ("A" if sig[-1] != "A" else "B")                            # 서명 마지막 글자를 반드시 다르게
        self.assertNotGraded(self.run_slide(token=f"{head}.{body}.{changed_sig}"), "token_invalid")
        self.assertNotGraded(self.run_slide(user_idx=6), "token_invalid")                  # 다른 계정의 토큰
        self.assertNotGraded(self.run_slide(now=NOW + 7200), "token_expired")
        other_study = issue(self.manifest, self.ref, study_idx=4)
        self.assertNotGraded(self.run_slide(token=other_study), "token_invalid")           # 다른 문제의 토큰

    def test_presentation_changes_are_detected(self):
        newer = manifest_of(make_paths(24)[:5] + ["/synthetic/knee/new.png"] + make_paths(24)[5:])
        # 목록이 바뀌었고 새 목록에 대해 다시 승인됐다고 하자 → 옛 목록의 토큰은 새 기준으로 채점되지 않는다
        slide_refs.APPROVED_SLIDE_REFERENCES.clear()
        new_ref = make_ref(newer, 12, reference_id=9)
        self.approve(new_ref)
        self.assertNotGraded(self.run_slide(image=st_image(list(newer.paths)), token=self.token), "presentation_changed")
        # 같은 목록이지만 기준이 새 버전으로 교체됨
        slide_refs.APPROVED_SLIDE_REFERENCES.clear()
        self.approve(make_ref(self.manifest, 12, reference_id=10, version="v2"))
        self.assertNotGraded(self.run_slide(), "presentation_changed")
        # 같은 기준 번호·버전이지만 이미지 내용 해시가 바뀐 승인으로 교체됨
        slide_refs.APPROVED_SLIDE_REFERENCES.clear()
        self.approve(make_ref(self.manifest, 12, image_sha256=fake_sha(99)))
        self.assertNotGraded(self.run_slide(), "presentation_changed")

    def test_list_binding_alone_detects_a_changed_presentation(self):
        """기준 번호·버전·내용 해시·대표 번호는 모두 같고 목록 버전(또는 목록 해시)만 다른 토큰도 거절한다.
        대표가 아닌 장을 요청해도 이 검사가 먼저이므로 presentation_changed 이다(비대표 분기보다 앞)."""
        other_version = "ordered-paths-json-v1:" + "0" * 64
        for name, over in {"manifest_version": dict(manifest_version=other_version), "slice_set_sha256": dict(slice_set_sha256="0" * 64)}.items():
            token = issue(self.manifest, self.ref, **over)
            for index in ("12", "3"):
                with self.subTest(changed=name, index=index):
                    self.assertNotGraded(self.run_slide(index=index, token=token), "presentation_changed")

    def test_token_for_another_representative_slice_is_rejected(self):
        wrong = issue(self.manifest, self.ref, slice_no=3)
        self.assertNotGraded(self.run_slide(token=wrong), "presentation_changed")

    def test_old_list_with_new_approval_requires_a_fresh_token(self):
        # 승인은 옛 목록 기준인데 문제 목록이 바뀐 경우: 새 목록에는 적용되지 않아 채점하지 않는다
        newer = st_image(make_paths(24)[::-1])
        self.assertNotGraded(self.run_slide(image=newer), "no_reference_for_slice")

    def test_slice_index_errors_are_input_errors(self):
        for raw in (None, "", "-1", "1.5", "true", "24", "abc", "99"):
            with self.subTest(raw=raw):
                with self.assertRaises(slice_grade.SlideRequestError) as ctx:
                    self.run_slide(index=raw)
                self.assertEqual(ctx.exception.status_code, 400)

    def test_missing_signing_key_with_an_approved_representative_is_a_server_error(self):
        for value in ("", "short"):
            with mock.patch.dict(os.environ, {"KNEE_PROBLEM_TOKEN_KEY": value}):
                with self.assertRaises(slice_grade.SlideRequestError) as ctx:
                    self.run_slide()
                self.assertEqual(ctx.exception.status_code, 503)
                self.assertEqual(ctx.exception.detail, "이 기능은 지금 사용할 수 없습니다.")   # 구성 내용을 알리지 않는 고정 문구

    def test_unusable_list_is_unavailable_and_checked_after_index_syntax(self):
        self.assertNotGraded(self.run_slide(image="[]"), "study_image_unavailable")
        self.assertNotGraded(self.run_slide(image=json.dumps(["/other/a.png"])), "study_image_unavailable")
        with mock.patch.dict(os.environ, {"KNEE_ASSET_ROOTS": ""}):
            self.assertNotGraded(self.run_slide(), "study_image_unavailable")                  # 루트 미설정이면 닫힘
        with self.assertRaises(slice_grade.SlideRequestError):
            self.run_slide(image="[]", index=None)                                             # 형식 오류가 먼저

    def test_asset_verifier_hook(self):
        calls = []

        def verifier(ref, manifest):
            calls.append((ref.reference_id, manifest.count))
            return self.verdict

        self.verdict = None
        self.assertTrue(self.run_slide(asset_verifier=verifier).result["graded"])
        self.assertEqual(calls, [(7, 24)])
        for reason in ("asset_changed", "study_image_unavailable"):
            self.verdict = reason
            self.assertNotGraded(self.run_slide(asset_verifier=verifier), reason)
        self.verdict = "something unexpected"
        self.assertNotGraded(self.run_slide(asset_verifier=verifier), "asset_changed")      # 알 수 없는 사유도 안전하게 미채점
        calls.clear()
        self.run_slide(index="3", asset_verifier=verifier)                                  # 비대표 장에서는 호출하지 않는다
        self.run_slide(token=None, asset_verifier=verifier)
        self.assertEqual(calls, [])

    def test_reference_that_rasterizes_to_nothing_is_not_graded(self):
        slide_refs.APPROVED_SLIDE_REFERENCES.clear()
        ref = make_ref(self.manifest, 12, width=0.0004)
        self.approve(ref)
        out = self.run_slide(token=issue(self.manifest, ref))
        self.assertFalse(out.result["graded"])
        self.assertEqual(out.result["not_graded_reason"], "reference_not_approved")
        self.assertIsNone(out.image_path)

    def test_integer_verdict_still_applies(self):
        out = self.run_slide(mask=box_mask(x=0.1, y=0.3, width=0.4, height=0.4))            # 겹침 1/3 → 부분정답
        self.assertEqual(out.result["result"], "부분정답")
        far = self.run_slide(mask=box_mask(x=0.0, y=0.0, width=0.1, height=0.1))
        self.assertEqual(far.result["result"], "오답")
        self.assertEqual(far.image_path, make_paths()[12])                                  # 오답도 대표 장이므로 저장 대상


# ── 제시 응답 빌더 ────────────────────────────────────────────────────────────
class KneeTaskTests(SyntheticBase):
    FORBIDDEN_KEYS = {"x", "y", "width", "height", "reference_bbox", "reference", "st_disease", "diagnosis", "source", "version",
                      "reviewed_by", "reviewed_at", "image_path", "plane", "image_sha256", "slice_set_sha256", "reference_id"}

    def setUp(self):
        super().setUp()
        self.manifest = manifest_of()
        self.ref = make_ref(self.manifest)

    def task(self, **over):
        args = dict(user_idx=5, study_idx=3, st_image=st_image(), image_url="/serve/authorized/abc", image_width=320, image_height=320, now=NOW)
        args.update(over)
        return slice_grade.build_knee_task(**args)

    def test_available_task_has_representative_info_and_a_working_token(self):
        self.approve(self.ref)
        body = self.task()
        task = body["knee_task"]
        self.assertEqual(body["study_idx"], 3)
        self.assertEqual((task["schema_version"], task["mode"]), ("knee-representative-v1", "representative_slice"))
        self.assertEqual((task["representative_slice_index"], task["display_slice_number"], task["slice_count"]), (12, 13, 24))
        self.assertEqual((task["image_url"], task["image_width"], task["image_height"]), ("/serve/authorized/abc", 320, 320))
        self.assertEqual(task["coord_system"], "normalized_image_top_left")
        self.assertTrue(task["grading_available"])
        self.assertIsNone(task["unavailable_reason"])
        self.assertIn("ACL", task["task_label"])
        self.assertNotIn("병변", task["task_label"])
        self.assertTrue(set(task).isdisjoint(self.FORBIDDEN_KEYS))
        text = json.dumps(body, ensure_ascii=False)
        for secret in (self.ref.reviewed_by, self.ref.source, "synthetic/knee/012", fake_sha(12)):
            self.assertNotIn(secret, text)
        # 이 토큰으로 제출하면 채점된다
        out = slice_grade.grade_slide(user_mask=box_mask(), user_idx=5, study_idx=3, st_image=st_image(), slice_index_raw="12",
                                      problem_token=task["problem_token"], now=NOW + 60)
        self.assertTrue(out.result["graded"])

    def test_without_an_approved_representative_nothing_is_presented_as_approved(self):
        for expected, rows in (("no_reference_for_slice", []), ("reference_ambiguous", [self.ref, make_ref(self.manifest, 5, reference_id=8)])):
            slide_refs.APPROVED_SLIDE_REFERENCES.clear()
            for row in rows:
                self.approve(row)
            with self.subTest(expected=expected):
                task = self.task()["knee_task"]
                self.assertFalse(task["grading_available"])
                self.assertEqual(task["unavailable_reason"], expected)
                for key in ("problem_token", "image_url", "image_width", "image_height", "representative_slice_index", "display_slice_number"):
                    self.assertIsNone(task[key], key)
                self.assertEqual(task["slice_count"], 24)

    def test_unusable_list_is_unavailable(self):
        task = self.task(st_image="[]")["knee_task"]
        self.assertEqual((task["grading_available"], task["unavailable_reason"], task["slice_count"]), (False, "study_image_unavailable", None))

    def test_caller_must_not_hand_over_the_raw_path_or_bad_dimensions(self):
        self.approve(self.ref)
        for over in (dict(image_url=self.ref.image_path), dict(image_url=""), dict(image_url=None), dict(image_width=0), dict(image_height=-1),
                     dict(image_width=True), dict(image_height=320.0)):
            with self.subTest(over=over):
                with self.assertRaises(ValueError):
                    self.task(**over)

    def test_missing_key_with_an_approved_representative_is_a_server_error(self):
        self.approve(self.ref)
        with mock.patch.dict(os.environ, {"KNEE_PROBLEM_TOKEN_KEY": ""}):
            with self.assertRaises(slice_grade.SlideRequestError) as ctx:
                self.task()
        self.assertEqual(ctx.exception.status_code, 503)

    def test_token_lifetime_follows_the_configured_ttl(self):
        self.approve(self.ref)
        with mock.patch.dict(os.environ, {"KNEE_PROBLEM_TOKEN_TTL_S": "900"}):
            token = self.task()["knee_task"]["problem_token"]
        claims = tokens.verify_token(token, key=KEY.encode(), now=NOW + 1, user_idx=5, study_idx=3)
        self.assertEqual(claims["exp"] - claims["iat"], 900)


# ── AC-02/03/05: HTTP 흐름(가짜 DB·이벤트) ───────────────────────────────────────
class RouterSlideTests(SyntheticBase):
    FORM = {"study_idx": "3", "roi_type": "box", "x": "0.3", "y": "0.3", "width": "0.4", "height": "0.4"}

    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {"KNEE_ROI_GRADE_ENABLED": "1", "KNEE_SLICE_READING_ENABLED": "0"})
        env.start()
        self.addCleanup(env.stop)
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="test-secret")

        @app.post("/_login")
        async def _login(request: Request, idx: int = 5, verified: bool = True):
            request.session["learner"] = {"idx": idx, "email_verified": verified}
            return {"ok": True}

        app.include_router(kneeLearning.router)
        # 가짜 DB 세션: 영상 검증·저장 사이 재대조(db.refresh)를 위해 refresh 만 흉내 낸다(문제 행은 시험이 직접 들고 있다).
        app.dependency_overrides[get_db] = lambda: SimpleNamespace(refresh=lambda obj: None)
        # 영상 공급원이 없는 합성 시험이므로 "영상 검증 생략"을 **명시로** 주입한다. 운영 코드에는 생략 경로가 없다(없으면 제출이 닫힌다).
        kneeLearning.set_asset_verifier(lambda reference, manifest: None)
        self.addCleanup(kneeLearning.set_asset_verifier, None)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.created, self.published = [], []
        self.study = SimpleNamespace(idx=3, del_yn="N", st_part="4", st_modal="3", st_image=st_image(), st_disease="ACL 파열")

        def fake_create(db, **kw):
            self.created.append(kw)
            return SimpleNamespace(idx=11, rn_image=kw["rn_image"], rn_solution=kw["rn_solution"])

        async def fake_publish(name, payload):
            self.published.append((name, payload))

        patches = [
            mock.patch.object(kneeLearning.review_node_repositories, "create", fake_create),
            mock.patch.object(kneeLearning.review_events, "publish", fake_publish),
            mock.patch.object(kneeLearning.study_repositories, "get_by_idx", lambda db, idx: self.study if idx == 3 else None),
            mock.patch.object(kneeLearning.learning_service, "get_random_problem",
                              lambda db, **kw: SimpleNamespace(idx=99, st_part="4", st_modal="3", st_image="/img/99.png")),
            mock.patch.object(kneeLearning.user_service, "get_user", lambda db, idx: SimpleNamespace(idx=idx)),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.manifest = manifest_of()
        self.ref = make_ref(self.manifest)

    def login(self, idx=5, verified=True):
        self.assertEqual(self.client.post("/_login", params={"idx": idx, "verified": str(verified).lower()}).status_code, 200)

    def real_token(self, user_idx=5):
        return slice_grade.build_knee_task(user_idx=user_idx, study_idx=3, st_image=self.study.st_image, image_url="/serve/x",
                                           image_width=320, image_height=320, now=int(time.time()))["knee_task"]["problem_token"]

    def post(self, data=None):
        form = dict(self.FORM)
        form.update(data or {})
        return self.client.post("/learning/roi-grade/knee", data=form)

    def ready(self):
        self.login()
        self.approve(self.ref)
        return self.real_token()

    def assertNothingSaved(self):
        self.assertEqual(self.created, [])
        self.assertEqual(self.published, [])

    def test_representative_slice_is_graded_saved_with_a_single_server_path_and_event_follows(self):
        token = self.ready()
        res = self.post({"slice_index": "12", "problem_token": token})
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertTrue(body["graded"])
        self.assertEqual((body["result"], body["slice_index"], body["reference_basis"], body["clinical_validated"]), ("정답", 12, "dataset_acl_region", False))
        self.assertEqual(len(self.created), 1)
        saved = self.created[0]
        self.assertEqual(saved["rn_image"], make_paths()[12])
        self.assertNotIn("[", saved["rn_image"])                        # 배열 문자열이 아니다
        self.assertEqual((saved["rn_u_idx"], saved["rn_s_idx"], saved["rn_solution"]), (5, 3, "C"))
        self.assertEqual(len(self.published), 1)
        self.assertEqual(self.published[0][1]["rn_image"], make_paths()[12])
        self.assertEqual(body["review_idx"], 11)
        self.assertEqual(body["next"]["idx"], 99)

    def test_without_any_verifier_the_submit_is_closed_not_skipped(self):
        """Codex 20차 ASSET-020-01: 공급원도 시험의 명시 주입도 없으면 유효한 토큰으로도 채점·저장·이벤트가 없다(검증 생략 금지)."""
        kneeLearning.set_asset_verifier(None)
        token = self.ready()
        for _ in range(2):
            res = self.post({"slice_index": "12", "problem_token": token})
            self.assertEqual(res.status_code, 200, res.text)
            body = res.json()
            self.assertEqual((body["graded"], body["not_graded_reason"], body["review_idx"], body["result"]), (False, "study_image_unavailable", None, None))
        self.assertEqual((self.created, self.published), ([], []))

    def test_state_change_after_verification_discards_the_result(self):
        """Codex 20차 ASSET-020-02: 영상 검증이 끝난 뒤 저장 전에 승인이 철회되면 저장하지 않는다."""
        token = self.ready()

        def verifier(reference, manifest):
            slide_refs.APPROVED_SLIDE_REFERENCES.pop(3, None)       # 영상을 읽는 동안 승인이 철회됐다
            return None
        kneeLearning.set_asset_verifier(verifier)
        res = self.post({"slice_index": "12", "problem_token": token})
        body = res.json()
        self.assertEqual((body["graded"], body["not_graded_reason"], body["review_idx"]), (False, "presentation_changed", None))
        self.assertEqual((self.created, self.published), ([], []))

    def test_non_representative_slice_is_not_graded_saved_or_published(self):
        token = self.ready()
        for index in ("0", "11", "13", "23"):
            res = self.post({"slice_index": index, "problem_token": token})
            self.assertEqual(res.status_code, 200, res.text)
            self.assertFalse(res.json()["graded"])
            self.assertEqual(res.json()["not_graded_reason"], "no_reference_for_slice")
        self.assertNothingSaved()

    def test_request_cannot_choose_the_path_reference_plane_or_user(self):
        token = self.ready()
        evil = {"image_path": "/evil.png", "image_sha256": "0" * 64, "reference_id": "999", "plane": "axial", "user_idx": "77",
                "rn_u_idx": "77", "slice_set_sha256": "f" * 64, "x_ref": "0.9"}
        res = self.post({"slice_index": "12", "problem_token": token, **evil})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.created[0]["rn_image"], make_paths()[12])
        self.assertEqual(self.created[0]["rn_u_idx"], 5)
        self.assertEqual(res.json()["plane"], "sagittal")
        self.assertNotIn("evil", res.text)

    def test_slice_index_and_roi_errors_are_400_and_save_nothing(self):
        token = self.ready()
        for data in ({}, {"slice_index": ""}, {"slice_index": "-1"}, {"slice_index": "1.5"}, {"slice_index": "true"}, {"slice_index": "24"},
                     {"slice_index": "12", "roi_type": "circle"}, {"slice_index": "12", "x": "-0.1"}, {"slice_index": "12", "x": "0.8", "width": "0.4"},
                     {"slice_index": "12", "width": "nan"}, {"slice_index": "12", "height": "inf"}, {"slice_index": "12", "width": "0"}):
            with self.subTest(data=data):
                res = self.post({"problem_token": token, **data})
                self.assertEqual(res.status_code, 400, res.text)
        self.assertNothingSaved()

    def test_missing_or_bad_token_is_not_graded(self):
        self.ready()
        for token in (None, "", "garbage"):
            res = self.post({"slice_index": "12", **({"problem_token": token} if token is not None else {})})
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(res.json()["not_graded_reason"], "token_invalid")
        self.assertNothingSaved()

    def test_other_users_token_is_not_graded(self):
        self.login(idx=5)
        self.approve(self.ref)
        other_token = self.real_token(user_idx=6)
        res = self.post({"slice_index": "12", "problem_token": other_token})
        self.assertEqual(res.json()["not_graded_reason"], "token_invalid")
        self.assertNothingSaved()

    def test_no_approved_representative_is_not_graded_and_needs_no_token(self):
        self.login()
        res = self.post({"slice_index": "12"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["not_graded_reason"], "no_reference_for_slice")
        self.assertNothingSaved()

    def test_every_not_graded_response_has_review_idx_null(self):
        """미채점 응답은 사유와 관계없이 review_idx 키가 있고 null 이다(015-02). 저장이 일어나면 실제 번호로 바뀐다."""
        token = self.ready()
        real_now = int(time.time())
        two_rows = [self.ref, make_ref(self.manifest, 5, reference_id=8)]
        scenarios = {
            "non-representative slice": (None, dict(slice_index="3", problem_token=token)),
            "missing token": (None, dict(slice_index="12")),
            "garbage token": (None, dict(slice_index="12", problem_token="garbage")),
            "presentation changed": (None, dict(slice_index="12", problem_token=issue(self.manifest, self.ref, slice_no=3, now=real_now))),
            "unusable list": ("[]", dict(slice_index="12", problem_token=token)),
        }
        for name, (image, data) in scenarios.items():
            with self.subTest(case=name):
                try:
                    if image is not None:
                        self.study = SimpleNamespace(**{**self.study.__dict__, "st_image": image})
                    body = self.post(data).json()
                finally:                                               # 한 사례가 실패해도 다음 사례에 영향이 가지 않게 되돌린다
                    self.study = SimpleNamespace(**{**self.study.__dict__, "st_image": st_image()})
                self.assertFalse(body["graded"])
                self.assertIn("review_idx", body)
                self.assertIsNone(body["review_idx"])
        # 토큰 만료
        with mock.patch.object(kneeLearning, "time", SimpleNamespace(time=lambda: real_now + 10**6)):
            body = self.post({"slice_index": "12", "problem_token": token}).json()
        self.assertEqual(body["not_graded_reason"], "token_expired")
        self.assertIn("review_idx", body)
        self.assertIsNone(body["review_idx"])
        # 승인이 둘 이상
        slide_refs.APPROVED_SLIDE_REFERENCES[3] = two_rows
        body = self.post({"slice_index": "12", "problem_token": token}).json()
        self.assertEqual(body["not_graded_reason"], "reference_ambiguous")
        self.assertIn("review_idx", body)
        self.assertIsNone(body["review_idx"])
        # 승인 없음
        slide_refs.APPROVED_SLIDE_REFERENCES.clear()
        body = self.post({"slice_index": "12"}).json()
        self.assertEqual(body["not_graded_reason"], "no_reference_for_slice")
        self.assertIn("review_idx", body)
        self.assertIsNone(body["review_idx"])
        self.assertNothingSaved()

    def test_graded_response_replaces_review_idx_with_the_saved_number(self):
        token = self.ready()
        body = self.post({"slice_index": "12", "problem_token": token}).json()
        self.assertTrue(body["graded"])
        self.assertEqual(body["review_idx"], 11)

    def test_not_graded_and_graded_bodies_share_the_contract_keys(self):
        token = self.ready()
        graded = self.post({"slice_index": "12", "problem_token": token}).json()
        not_graded = self.post({"slice_index": "3", "problem_token": token}).json()
        core = {"ok", "graded", "not_graded_reason", "not_graded_message", "result", "iou", "overlap_px", "rule", "user_roi_bbox",
                "reference_bbox", "reference", "body_part", "plane", "notices", "review_idx", "next"}
        self.assertTrue(core <= set(graded))
        self.assertTrue(core <= set(not_graded))

    def test_save_failure_is_a_safe_502_without_event(self):
        token = self.ready()

        def failing(db, **kw):
            raise RuntimeError("password=hunter2 host=10.0.0.5")

        with mock.patch.object(kneeLearning.review_node_repositories, "create", failing):
            res = self.post({"slice_index": "12", "problem_token": token})
        self.assertEqual(res.status_code, 502)
        for secret in ("hunter2", "10.0.0.5", token):
            self.assertNotIn(secret, res.text)
        self.assertEqual(self.published, [])

    def test_missing_signing_key_with_an_approved_representative_is_503(self):
        token = self.ready()
        with mock.patch.dict(os.environ, {"KNEE_PROBLEM_TOKEN_KEY": ""}):
            res = self.post({"slice_index": "12", "problem_token": token})
        self.assertEqual(res.status_code, 503)
        self.assertNothingSaved()

    def test_access_rules_apply_to_slide_studies_too(self):
        self.approve(self.ref)
        token = self.real_token()
        self.assertNotEqual(self.post({"slice_index": "12", "problem_token": token}).status_code, 401)
        self.login(verified=False)
        self.assertNotEqual(self.post({"slice_index": "12", "problem_token": token}).status_code, 403)
        saved_before = len(self.created)
        published_before = len(self.published)
        with mock.patch.dict(os.environ, {"KNEE_ROI_GRADE_ENABLED": ""}):
            with mock.patch.object(knee_config, "_read_dotenv", lambda key: ""):
                self.assertEqual(self.post({"slice_index": "12", "problem_token": token}).status_code, 503)
        self.assertEqual(len(self.created), saved_before)
        self.assertEqual(len(self.published), published_before)

    def test_single_image_studies_ignore_the_slide_fields(self):
        self.login()
        single = import_module("class.knee.reference_rois")
        self.study = SimpleNamespace(**{**self.study.__dict__, "st_image": "/img/knee3.png"})
        with mock.patch.dict(single.APPROVED_REFERENCES, {3: single.ReferenceRoi(
                study_idx=3, plane="sagittal", x=0.3, y=0.3, width=0.4, height=0.4, source="synthetic", version="v1",
                reviewed_by="reviewer-000", reviewed_at="2026-10-12")}, clear=True):
            res = self.post({"slice_index": "not-a-number", "problem_token": "garbage"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["graded"])
        self.assertEqual(self.created[0]["rn_image"], "/img/knee3.png")


# ── 좌표 저장 계약: 마스크 → 저장용 박스 → 다시 래스터라이즈 ─────────────────────────────
class StoredBoxTests(unittest.TestCase):
    G = knee_grade.GRID

    def mask_from_cells(self, x1, x2, y1, y2):
        mask = np.zeros((self.G, self.G), dtype=bool)
        mask[y1:y2, x1:x2] = True
        return mask

    def rasterize(self, stored):
        return knee_grade.user_mask(roi_type="box", **stored)

    def test_stored_box_reproduces_the_same_mask_and_verdict(self):
        rng = random.Random(20261012)
        ref = box_mask(x=0.2, y=0.2, width=0.5, height=0.5)
        for _ in range(1500):
            x1, y1 = rng.randrange(self.G), rng.randrange(self.G)
            x2, y2 = rng.randrange(x1 + 1, self.G + 1), rng.randrange(y1 + 1, self.G + 1)
            mask = self.mask_from_cells(x1, x2, y1, y2)
            stored = knee_grade.stored_box_from_mask(mask)
            back = self.rasterize(stored)
            self.assertTrue(np.array_equal(back, mask), stored)
            inter, union = int(np.logical_and(mask, ref).sum()), int(np.logical_or(mask, ref).sum())
            self.assertEqual((int(np.logical_and(back, ref).sum()), int(np.logical_or(back, ref).sum())), (inter, union))
            self.assertEqual(knee_grade.verdict_from_px(inter, union), knee_grade.verdict_from_px(int(np.logical_and(back, ref).sum()), int(np.logical_or(back, ref).sum())))

    def test_stored_values_satisfy_the_db_box_conditions(self):
        for x1, x2 in ((0, 1), (0, self.G), (self.G - 1, self.G), (1, 2), (255, 256), (3, 509)):
            for y1, y2 in ((0, self.G), (self.G - 1, self.G), (7, 8)):
                stored = knee_grade.stored_box_from_mask(self.mask_from_cells(x1, x2, y1, y2))
                self.assertGreaterEqual(stored["x"], 0)
                self.assertGreaterEqual(stored["y"], 0)
                self.assertGreater(stored["width"], 0)
                self.assertGreater(stored["height"], 0)
                self.assertLessEqual(Decimal(str(stored["x"])) + Decimal(str(stored["width"])), 1)
                self.assertLessEqual(Decimal(str(stored["y"])) + Decimal(str(stored["height"])), 1)

    def test_half_pixel_case_rounding_the_request_would_change_the_mask_but_the_mask_based_box_does_not(self):
        raw = dict(x=0.5 / self.G, y=0.0, width=10 / self.G, height=10 / self.G)
        mask = knee_grade.user_mask(roi_type="box", **raw)
        naive = knee_grade.user_mask(roi_type="box", **{k: round(v, 6) for k, v in raw.items()})
        self.assertFalse(np.array_equal(mask, naive))                         # 요청 원좌표를 반올림하면 달라진다
        self.assertEqual(int(mask.sum()), 100)
        self.assertEqual((int(np.logical_and(mask, naive).sum()), int(np.logical_or(mask, naive).sum())), (90, 110))
        self.assertTrue(np.array_equal(self.rasterize(knee_grade.stored_box_from_mask(mask)), mask))

    def test_slightly_outside_requests_become_storable_values(self):
        for raw in (dict(x=-5e-7, y=0.2, width=0.3, height=0.3), dict(x=0.7, y=0.2, width=0.3 + 5e-7, height=0.3)):
            mask = knee_grade.user_mask(roi_type="box", **raw)
            stored = knee_grade.stored_box_from_mask(mask)
            self.assertTrue(raw["x"] < 0 or raw["x"] + raw["width"] > 1)       # 요청 원값은 DB 조건을 어긴다
            self.assertGreaterEqual(stored["x"], 0)
            self.assertLessEqual(Decimal(str(stored["x"])) + Decimal(str(stored["width"])), 1)
            self.assertTrue(np.array_equal(self.rasterize(stored), mask))

    def test_empty_mask_cannot_be_stored(self):
        with self.assertRaises(ValueError):
            knee_grade.stored_box_from_mask(np.zeros((self.G, self.G), dtype=bool))


class DefaultStateTests(unittest.TestCase):
    def test_tables_are_empty_and_feature_is_off_by_default(self):
        data = import_module("class.knee.slide_references_data").REFERENCE_ROWS   # 승인표 = 데이터 파일 행(기본 비어 있음)
        self.assertEqual(sorted((r.study_idx, r.reference_id) for rows in slide_refs.APPROVED_SLIDE_REFERENCES.values() for r in rows), sorted((r["study_idx"], r["reference_id"]) for r in data))
        source = (Path(__file__).resolve().parents[1] / "routers" / "kneeLearning.py").read_text(encoding="utf-8")
        self.assertIn('"KNEE_ROI_GRADE_ENABLED", "0"', source)

    def test_new_modules_use_no_model_network_or_storage_apis(self):
        root = Path(__file__).resolve().parents[1]
        for rel in ("class/knee/slice_manifest.py", "class/knee/slide_references.py", "class/knee/problem_token.py",
                    "services/kneeSliceGradeServices.py"):
            text = (root / rel).read_text(encoding="utf-8")
            for banned in ("import torch", "ultralytics", "hf_hub_download", "requests", "httpx", "nas_service", "open(", "UploadFile",
                           "sqlalchemy", "review_node_repositories"):
                self.assertNotIn(banned, text, f"{rel}: {banned}")


if __name__ == "__main__":
    unittest.main()
