"""노출 팝업 등록 시 표시 순서가 다음 번호가 되는지 확인한다."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from routers.admin import router
from services import popupServices as popup_service


class PopupSortTests(unittest.TestCase):
    def test_first_exposed_starts_at_zero(self) -> None:
        self.assertEqual(popup_service.next_exposed_sort(None), 0)

    def test_existing_exposed_increments(self) -> None:
        self.assertEqual(popup_service.next_exposed_sort(0), 1)
        self.assertEqual(popup_service.next_exposed_sort(4), 5)

    def test_exposed_create_ignores_typed_sort(self) -> None:
        saved: dict = {}
        original_max = popup_service.repositories.max_exposed_sort
        original_create = popup_service.repositories.create

        def fake_create(db, **kwargs):
            saved.update(kwargs)
            return SimpleNamespace(idx=1, **kwargs)

        popup_service.repositories.max_exposed_sort = lambda db: 2
        popup_service.repositories.create = fake_create
        try:
            popup_service.create_popup(
                object(),
                pp_title="오픈",
                pp_image="/a.png",
                pp_sort=99,
                state="N",
            )
        finally:
            popup_service.repositories.max_exposed_sort = original_max
            popup_service.repositories.create = original_create

        self.assertEqual(saved["pp_sort"], 3)
        self.assertEqual(saved["state"], "N")

    def test_hidden_create_keeps_given_sort(self) -> None:
        saved: dict = {}
        original_create = popup_service.repositories.create

        def fake_create(db, **kwargs):
            saved.update(kwargs)
            return SimpleNamespace(idx=1, **kwargs)

        popup_service.repositories.create = fake_create
        try:
            popup_service.create_popup(
                object(),
                pp_title="숨김",
                pp_image="/b.png",
                pp_sort=9,
                state="S",
            )
        finally:
            popup_service.repositories.create = original_create

        self.assertEqual(saved["pp_sort"], 9)
        self.assertEqual(saved["state"], "S")

    def test_next_sort_route_is_registered(self) -> None:
        paths = {getattr(route, "path", None) for route in router.routes}
        self.assertIn("/admin/popups/next-sort", paths)
