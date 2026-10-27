"""용어사전 영상 종류 값: 화면이 보내는 X-ray 도 xray 로 받는지 확인한다."""
import pytest

from services import learningServices


@pytest.mark.parametrize("given", ["X-ray", "xray", " XRAY ", "x-ray"])
def test_xray_spellings_are_accepted(monkeypatch, given):
    seen = {}
    monkeypatch.setattr(learningServices.repositories, "get_by_body_part",
                        lambda db, part, modality=None: seen.update(part=part, modality=modality) or [])
    learningServices.get_by_body_part(None, "chest", modality=given)
    assert seen == {"part": "chest", "modality": "xray"}


def test_unknown_modality_still_rejected():
    with pytest.raises(ValueError):
        learningServices.get_by_body_part(None, "chest", modality="pet")
