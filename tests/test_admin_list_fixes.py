"""관리자 목록 수정: 학습 목록 요약·회원 인원 집계 기준."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest import mock

from services import studyServices
from repositories import userRepositories


class StudyListSummaryTests(unittest.TestCase):
    def test_slide_json_becomes_count_label(self) -> None:
        paths = [f"/stylesheets/assets/brain/CT/{i:02d}.png" for i in range(64)]
        raw = json.dumps(paths, ensure_ascii=False)
        self.assertEqual(studyServices.summarize_st_image_for_list(raw), "슬라이드 64장")

    def test_single_path_unchanged(self) -> None:
        path = "/stylesheets/assets/brain/CT/one.png"
        self.assertEqual(studyServices.summarize_st_image_for_list(path), path)

    def test_list_studies_for_admin_does_not_send_full_slide_json(self) -> None:
        paths = [f"/img/{i}.png" for i in range(3)]
        row = SimpleNamespace(
            idx=1,
            del_yn="N",
            st_part="1",
            st_modal="2",
            st_image=json.dumps(paths),
            st_disease="출혈",
            created_at=None,
            updated_at=None,
            state="N",
        )
        with mock.patch.object(studyServices.repositories, "list_all", return_value=[row]):
            items = studyServices.list_studies_for_admin(object())
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["st_image"], "슬라이드 3장")
        self.assertNotIn("/img/0.png", items[0]["st_image"])


class MemberCountAlignTests(unittest.TestCase):
    def test_member_filters_exclude_admin(self) -> None:
        filters = userRepositories._member_list_filters(active_only=True)
        self.assertEqual(len(filters), 3)
