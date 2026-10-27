"""
대표 장 승인표를 데이터 파일(class/knee/slide_references_data.py)에서 불러오는 규칙 시험(DB·서버 없음).

- 데이터 파일이 비어 있으면 승인표도 비어 있다(기본, 어떤 문제도 채점하지 않음).
- 데이터 파일의 행은 study_idx 별로 승인표에 올라가고, 다른 study 에는 쓰이지 않는다.
- 형식이 틀린 행이 있으면 조용히 넘기지 않고 불러오기가 실패한다.
- 데이터 파일 모듈이 없으면 빈 표로 남는다.
실행: python -m pytest tests/test_knee_reference_data.py
"""
import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

refs = importlib.import_module("class.knee.slide_references")
manifest_mod = importlib.import_module("class.knee.slice_manifest")
DATA_MOD = "class.knee.slide_references_data"


def row(study_idx=700, reference_id=1001, **over):
    paths = [f"/knee_series/case-x/slice_{i:03d}.png" for i in range(5)]
    man = manifest_mod.build_manifest(json.dumps(paths), ("/knee_series/",))
    fields = dict(
        reference_id=reference_id, study_idx=study_idx, manifest_version=man.version, slice_set_sha256=man.list_sha256,
        slice_no=2, image_path=paths[2], image_sha256="a" * 64, plane="sagittal", x=0.3, y=0.3, width=0.2, height=0.2,
        source="synthetic", version="v1", reviewed_by="reviewer-000", reviewed_at="2026-10-16T00:00:00",
        source_slice_no=2, selection_reason="", image_width=320, image_height=320,
    )
    fields.update(over)
    return fields, man


class ReferenceDataLoaderTests(unittest.TestCase):
    def load_with(self, rows):
        """가짜 데이터 모듈로 불러오기 함수를 실행한 결과 표(실제 승인표는 건드리지 않는다)."""
        fake = types.ModuleType(DATA_MOD)
        fake.REFERENCE_ROWS = rows
        with mock.patch.dict(sys.modules, {DATA_MOD: fake}), mock.patch.dict(refs.APPROVED_SLIDE_REFERENCES, {}, clear=True):
            refs._load_reference_rows()
            return {k: list(v) for k, v in refs.APPROVED_SLIDE_REFERENCES.items()}

    def test_real_data_file_matches_table(self):
        data = importlib.import_module(DATA_MOD).REFERENCE_ROWS
        table = sorted((r.study_idx, r.reference_id) for rows in refs.APPROVED_SLIDE_REFERENCES.values() for r in rows)
        self.assertEqual(table, sorted((r["study_idx"], r["reference_id"]) for r in data))

    def test_empty_data_file_means_empty_table(self):
        self.assertEqual(self.load_with([]), {})

    def test_rows_are_loaded_per_study_and_resolve_only_for_their_study(self):
        a, man = row(700, 1001)
        b, _ = row(701, 1002, x=0.1)
        table = self.load_with([a, b])
        self.assertEqual(sorted(table), [700, 701])
        with mock.patch.dict(refs.APPROVED_SLIDE_REFERENCES, table, clear=True):
            self.assertEqual(refs.resolve(700, man).reference.x, 0.3)
            self.assertEqual(refs.resolve(701, man).reference.x, 0.1)
            self.assertEqual(refs.resolve(702, man).status, "none")   # 기준 없는 케이스: 채점 안 함

    def test_two_rows_for_one_study_are_ambiguous_not_first_wins(self):
        a, man = row(700, 1001)
        b, _ = row(700, 1002)
        with mock.patch.dict(refs.APPROVED_SLIDE_REFERENCES, self.load_with([a, b]), clear=True):
            self.assertEqual(refs.resolve(700, man).status, "ambiguous")

    def test_malformed_row_fails_loudly(self):
        bad, _ = row()
        bad["unexpected"] = 1
        with self.assertRaises(TypeError):
            self.load_with([bad])
        missing, _ = row()
        del missing["image_sha256"]
        with self.assertRaises(TypeError):
            self.load_with([missing])

    def test_import_errors_inside_data_module_are_not_swallowed(self):
        """데이터 파일 자체가 없을 때만 빈 표. 데이터 파일 안의 불러오기 오류(다른 모듈 없음·ImportError)는 숨기지 않는다(FINDING-REFERENCE-004)."""
        real = importlib.import_module

        def raising(exc):
            def fake(name, *a, **k):
                if name == DATA_MOD:
                    raise exc
                return real(name, *a, **k)
            return fake
        inner_missing = ModuleNotFoundError("No module named 'some_helper'", name="some_helper")
        for exc in (inner_missing, ImportError("cannot import name 'x' from 'y'")):
            with mock.patch("importlib.import_module", raising(exc)), mock.patch.dict(refs.APPROVED_SLIDE_REFERENCES, {}, clear=True):
                with self.assertRaises(ImportError):
                    refs._load_reference_rows()
        own_missing = ModuleNotFoundError(f"No module named '{DATA_MOD}'", name=DATA_MOD)
        with mock.patch("importlib.import_module", raising(own_missing)), mock.patch.dict(refs.APPROVED_SLIDE_REFERENCES, {}, clear=True):
            refs._load_reference_rows()
            self.assertEqual(refs.APPROVED_SLIDE_REFERENCES, {})

    def test_missing_data_module_leaves_table_empty(self):
        with mock.patch.dict(sys.modules, {DATA_MOD: None}), mock.patch.dict(refs.APPROVED_SLIDE_REFERENCES, {}, clear=True):
            refs._load_reference_rows()
            self.assertEqual(refs.APPROVED_SLIDE_REFERENCES, {})


if __name__ == "__main__":
    unittest.main()
