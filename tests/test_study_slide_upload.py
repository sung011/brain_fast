"""MRI 슬라이드 등록 테스트.

한 문제(slide)는 파일 이름 순서로 study 1행 JSON에 저장하고,
형식이 아니면 거절한다. 파일마다 문제(each)는 기존처럼 행을 나눈다.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import get_db
from routers.admin import router
from routers import admin as admin_router
from schemas.studySchemas import slide_name_errors, slide_order_number
from utils.adminAuth import admin_session_guard


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[admin_session_guard] = lambda: {"idx": 1}

    def _db():
        yield object()

    app.dependency_overrides[get_db] = _db
    return app


class SlideNameTests(unittest.TestCase):
    def test_reads_leading_number(self) -> None:
        self.assertEqual(slide_order_number("01_case.png"), 1)
        self.assertEqual(slide_order_number("10_case.jpg"), 10)
        self.assertIsNone(slide_order_number("case.png"))

    def test_rejects_bad_and_duplicate_names(self) -> None:
        errors = slide_name_errors(["02_b.png", "01_a.JPG", "01_c.png", "plain.png"])
        self.assertTrue(any("plain.png" in item for item in errors))
        self.assertTrue(any("중복" in item for item in errors))


class SlideUploadApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(_app())
        self._nas = admin_router.nas_service_mod.nas_service
        self.uploaded: list[str] = []
        self.saved: list[dict] = []

        async def upload_bytes(data: bytes, name: str, remote_dir: str = ""):
            self.uploaded.append(name)
            return {"remote_path": f"{remote_dir}/{name}"}

        admin_router.nas_service_mod.nas_service = SimpleNamespace(
            enabled=True,
            upload_bytes=upload_bytes,
        )

        def create_study(db, *, st_part, st_modal, st_disease, st_image):
            self.saved.append(
                {
                    "st_part": st_part,
                    "st_modal": st_modal,
                    "st_disease": st_disease,
                    "st_image": st_image,
                }
            )
            return SimpleNamespace(
                idx=len(self.saved),
                st_part=st_part,
                st_modal=st_modal,
                st_disease=st_disease,
                st_image=st_image,
            )

        self._create = admin_router.study_service.create_study
        admin_router.study_service.create_study = create_study

    def tearDown(self) -> None:
        admin_router.nas_service_mod.nas_service = self._nas
        admin_router.study_service.create_study = self._create

    def test_slide_thirty_files_one_row_in_order(self) -> None:
        files = [
            ("files", (f"{index:02d}_03_{index - 1:04d}.png", b"png", "image/png"))
            for index in range(30, 0, -1)
        ]
        response = self.client.post(
            "/admin/study",
            data={
                "st_part": "1",
                "st_modal": "3",
                "st_disease": "배아종",
                "upload_mode": "slide",
            },
            files=files,
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(len(self.saved), 1)
        self.assertEqual(len(self.uploaded), 30)
        paths = json.loads(self.saved[0]["st_image"])
        self.assertEqual(len(paths), 30)
        self.assertIn("01_03_0000.png", paths[0])
        self.assertIn("30_03_0029.png", paths[29])
        for index, path in enumerate(paths, start=1):
            self.assertIn(f"{index:02d}_03_{index - 1:04d}.png", path)

    def test_slide_saves_one_row_sorted_by_filename(self) -> None:
        response = self.client.post(
            "/admin/study",
            data={
                "st_part": "1",
                "st_modal": "3",
                "st_disease": "뇌출혈",
                "upload_mode": "slide",
            },
            files=[
                ("files", ("10_late.png", b"c", "image/png")),
                ("files", ("02_mid.png", b"b", "image/png")),
                ("files", ("01_early.jpg", b"a", "image/jpeg")),
            ],
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(len(self.saved), 1)
        paths = json.loads(self.saved[0]["st_image"])
        self.assertEqual(len(paths), 3)
        self.assertIn("01_early.jpg", paths[0])
        self.assertIn("02_mid.png", paths[1])
        self.assertIn("10_late.png", paths[2])
        self.assertEqual(self.saved[0]["st_modal"], "3")

    def test_bad_filename_is_rejected_before_upload(self) -> None:
        response = self.client.post(
            "/admin/study",
            data={
                "st_part": "1",
                "st_modal": "3",
                "st_disease": "뇌출혈",
                "upload_mode": "slide",
            },
            files=[
                ("files", ("01_ok.png", b"a", "image/png")),
                ("files", ("scan.png", b"b", "image/png")),
            ],
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("01_이름", response.text)
        self.assertEqual(self.uploaded, [])
        self.assertEqual(self.saved, [])

    def test_slide_mode_requires_mri(self) -> None:
        response = self.client.post(
            "/admin/study",
            data={
                "st_part": "1",
                "st_modal": "1",
                "st_disease": "골절",
                "upload_mode": "slide",
            },
            files=[("files", ("01_x.png", b"a", "image/png"))],
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("MRI", response.text)
        self.assertEqual(self.saved, [])

    def test_each_mode_creates_one_row_per_file(self) -> None:
        response = self.client.post(
            "/admin/study",
            data={
                "st_part": "1",
                "st_modal": "3",
                "st_disease": "뇌출혈",
                "upload_mode": "each",
            },
            files=[
                ("files", ("a.png", b"a", "image/png")),
                ("files", ("b.png", b"b", "image/png")),
            ],
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["count"], 2)
        self.assertEqual(len(self.saved), 2)
        self.assertFalse(self.saved[0]["st_image"].startswith("["))

    def test_create_page_explains_slide_filename(self) -> None:
        response = self.client.get("/admin/study/create?partial=1")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("한 문제의 슬라이드", response.text)
        self.assertIn("01_이름.png", response.text)
        self.assertIn("01_이름.jpg", response.text)


if __name__ == "__main__":
    unittest.main()
