"""
main 병합(2026-10-13, 443660a "MRI ROI 채점에 슬라이스 식별 추가")이 무릎 학습 코드에 주는 영향 확인.

main 이 공용 쪽에 넣은 것: `schemas/studySchemas.parse_study_image_paths`(st_image 목록 읽기), `services/learningServices.resolve_roi_grade_slice`,
`POST /learning/roi-grade`(뇌/공용 ROI 채점)의 `slice_index`·`image_path`. 무릎 학습은 이것을 쓰지 않고 자기 계약(class/knee/slice_manifest,
`POST /learning/roi-grade/knee`)을 쓴다. 이 시험은 두 해석이 **같은 목록을 같은 번호로 읽는지**, **어디가 다른지**(무릎이 더 엄격해서 닫힘),
무릎 라우터가 main 의 새 함수에 의존하지 않는지를 고정한다. 공용 코드는 바꾸지 않는다(읽기만).
실행: python -m pytest tests/test_knee_main_slice_compat.py
"""
import json
import sys
import unittest
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from schemas.studySchemas import parse_study_image_paths
from services import learningServices as learning_service

manifest_mod = import_module("class.knee.slice_manifest")
ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOTS = ("/stylesheets/assets/knee/",)


def paths(n=5):
    return [f"/stylesheets/assets/knee/{i:03d}.png" for i in range(n)]


class SameListSameNumberTests(unittest.TestCase):
    def test_valid_knee_slide_lists_are_read_identically(self):
        """정상 목록은 main 의 읽기와 무릎 manifest 가 같은 경로를 같은 순서·같은 0 기반 번호로 읽는다."""
        for count in (1, 2, 5, 24):
            with self.subTest(count=count):
                raw = json.dumps(paths(count))
                self.assertEqual(parse_study_image_paths(raw), list(manifest_mod.build_manifest(raw, ASSET_ROOTS).paths))

    def test_whitespace_only_differences_keep_the_same_numbers(self):
        raw = json.dumps(paths(4), indent=3)
        self.assertEqual(parse_study_image_paths(raw), list(manifest_mod.build_manifest(raw, ASSET_ROOTS).paths))

    def test_main_slice_index_is_the_same_zero_based_array_position_as_knee(self):
        """main resolve_roi_grade_slice 의 slice_index 도 등록 배열 위치(0 기반)다 — 무릎 slice_index 와 같은 뜻."""
        study = SimpleNamespace(idx=3, del_yn="N", st_image=json.dumps(paths(6)))
        original = learning_service.study_repositories.get_by_idx
        learning_service.study_repositories.get_by_idx = lambda db, idx: study
        try:
            for index in range(6):
                info = learning_service.resolve_roi_grade_slice(None, study_idx=3, slice_index=index)
                self.assertEqual((info["slice_index"], info["image_path"]), (index, paths(6)[index]))
                self.assertEqual(manifest_mod.parse_slice_index(str(index), 6), index)
        finally:
            learning_service.study_repositories.get_by_idx = original


class KneeIsStricterThanMainTests(unittest.TestCase):
    """main 의 읽기는 너그럽고(공백 제거·빈 항목 버림·중복 허용) 무릎은 닫는다. 차이를 기록해 두어 무릎에서는 조용히 넘어가지 않게 한다."""

    def test_differences_close_the_knee_flow_instead_of_guessing(self):
        base = paths(3)
        cases = {
            "padded entry": json.dumps([f"  {base[0]}  ", base[1], base[2]]),
            "empty entry": json.dumps([base[0], "", base[2]]),
            "null entry": json.dumps([base[0], None, base[2]]),
            "duplicate entry": json.dumps([base[0], base[0], base[1]]),
            "backslash entry": json.dumps(["\\stylesheets\\assets\\knee\\000.png", base[1]]),
            "outside the allowed root": json.dumps(["/stylesheets/assets/brain/000.png", base[1]]),
        }
        for name, raw in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(manifest_mod.SliceManifestError):          # 무릎: 사용 불가(미채점/미제시)
                    manifest_mod.build_manifest(raw, ASSET_ROOTS)
        # 같은 입력을 main 은 읽어 버린다(정상 목록으로 보일 수 있다) — 서로 다른 번호가 될 수 있어 무릎은 추측하지 않는다.
        self.assertEqual(parse_study_image_paths(cases["padded entry"])[0], base[0])
        self.assertEqual(len(parse_study_image_paths(cases["empty entry"])), 2)    # 빈 항목을 버리면 번호가 한 칸씩 당겨진다
        self.assertEqual(len(parse_study_image_paths(cases["duplicate entry"])), 3)

    def test_dropping_empty_entries_would_shift_numbers_which_the_knee_flow_never_does(self):
        raw = json.dumps([paths(3)[0], "", paths(3)[1], paths(3)[2]])
        self.assertEqual(parse_study_image_paths(raw), [paths(3)[0], paths(3)[1], paths(3)[2]])   # main: 2번째가 1번 위치로 당겨짐
        with self.assertRaises(manifest_mod.SliceManifestError):
            manifest_mod.build_manifest(raw, ASSET_ROOTS)                                           # 무릎: 번호가 바뀌는 목록은 쓰지 않는다


class KneeRouteIndependenceTests(unittest.TestCase):
    def test_knee_router_and_services_do_not_depend_on_main_slice_resolution(self):
        for rel in ("routers/kneeLearning.py", "services/kneeSliceGradeServices.py", "services/kneeRoiGradeServices.py"):
            source = (ROOT / rel).read_text(encoding="utf-8")
            for name in ("resolve_roi_grade_slice", "parse_study_image_paths", "_find_slice_index_by_path"):
                self.assertNotIn(name, source, f"{rel}: {name}")

    def test_knee_router_only_uses_the_unchanged_random_problem_function_for_next(self):
        import inspect
        parameters = inspect.signature(learning_service.get_random_problem).parameters
        for name in ("st_part", "st_modal", "exclude_idxs", "user_idx"):
            self.assertIn(name, parameters)                      # 무릎 라우터가 next 를 고를 때 쓰는 인자가 그대로 있다


if __name__ == "__main__":
    unittest.main()
