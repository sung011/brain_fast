"""MRI roi-grade 슬라이스 매칭 테스트."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from schemas.studySchemas import parse_study_image_paths
from services import learningServices as learning_service


class ParseStudyImagePathsTests(unittest.TestCase):
    def test_single_path_is_not_multi_slice(self) -> None:
        self.assertIsNone(parse_study_image_paths("/stylesheets/assets/brain/CT/a.png"))

    def test_json_array(self) -> None:
        paths = [
            "/stylesheets/assets/brain/MRI/01_a.png",
            "/stylesheets/assets/brain/MRI/02_b.png",
        ]
        self.assertEqual(
            parse_study_image_paths(json.dumps(paths, ensure_ascii=False)),
            paths,
        )

    def test_empty_and_invalid(self) -> None:
        self.assertIsNone(parse_study_image_paths(None))
        self.assertIsNone(parse_study_image_paths("[]"))
        self.assertIsNone(parse_study_image_paths("not-json"))


class ResolveRoiGradeSliceTests(unittest.TestCase):
    def _mri_row(self, paths: list[str]) -> SimpleNamespace:
        return SimpleNamespace(
            del_yn="N",
            st_modal="3",
            st_image=json.dumps(paths, ensure_ascii=False),
        )

    def test_ct_ignores_slice_fields(self) -> None:
        row = SimpleNamespace(
            del_yn="N",
            st_modal="2",
            st_image="/stylesheets/assets/brain/CT/a.png",
        )
        with patch.object(learning_service.study_repositories, "get_by_idx", return_value=row):
            self.assertIsNone(
                learning_service.resolve_roi_grade_slice(
                    object(),
                    study_idx=1,
                    slice_index=0,
                    image_path="/anything.png",
                )
            )

    def test_mri_requires_slice_info(self) -> None:
        paths = [
            "/stylesheets/assets/brain/MRI/01_a.png",
            "/stylesheets/assets/brain/MRI/02_b.png",
        ]
        with patch.object(
            learning_service.study_repositories,
            "get_by_idx",
            return_value=self._mri_row(paths),
        ):
            with self.assertRaises(ValueError) as ctx:
                learning_service.resolve_roi_grade_slice(object(), study_idx=26)
        self.assertIn("slice_index", str(ctx.exception))

    def test_mri_by_slice_index(self) -> None:
        paths = [
            "/stylesheets/assets/brain/MRI/01_a.png",
            "/stylesheets/assets/brain/MRI/02_b.png",
        ]
        with patch.object(
            learning_service.study_repositories,
            "get_by_idx",
            return_value=self._mri_row(paths),
        ):
            resolved = learning_service.resolve_roi_grade_slice(
                object(), study_idx=26, slice_index=1
            )
        self.assertEqual(
            resolved,
            {
                "slice_index": 1,
                "image_path": paths[1],
                "slice_count": 2,
            },
        )

    def test_mri_by_image_path_basename(self) -> None:
        paths = [
            "/stylesheets/assets/brain/MRI/01_a.png",
            "/stylesheets/assets/brain/MRI/02_b.png",
        ]
        with patch.object(
            learning_service.study_repositories,
            "get_by_idx",
            return_value=self._mri_row(paths),
        ):
            resolved = learning_service.resolve_roi_grade_slice(
                object(), study_idx=26, image_path="02_b.png"
            )
        self.assertEqual(resolved["slice_index"], 1)

    def test_mri_out_of_range(self) -> None:
        paths = ["/stylesheets/assets/brain/MRI/01_a.png"]
        with patch.object(
            learning_service.study_repositories,
            "get_by_idx",
            return_value=self._mri_row(paths),
        ):
            with self.assertRaises(ValueError) as ctx:
                learning_service.resolve_roi_grade_slice(
                    object(), study_idx=26, slice_index=3
                )
        self.assertIn("범위를 벗어났습니다", str(ctx.exception))

    def test_mri_index_and_path_conflict(self) -> None:
        paths = [
            "/stylesheets/assets/brain/MRI/01_a.png",
            "/stylesheets/assets/brain/MRI/02_b.png",
        ]
        with patch.object(
            learning_service.study_repositories,
            "get_by_idx",
            return_value=self._mri_row(paths),
        ):
            with self.assertRaises(ValueError) as ctx:
                learning_service.resolve_roi_grade_slice(
                    object(),
                    study_idx=26,
                    slice_index=0,
                    image_path=paths[1],
                )
        self.assertIn("서로 다른 슬라이스", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
