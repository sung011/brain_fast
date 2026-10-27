"""Object Storage에 넣는 웹 경로에서 예전 NAS 접두어를 빼는지 확인한다."""

from services.objectStorageServices import public_object_path


def test_strips_public_root_and_builds_key() -> None:
    key, web_path = public_object_path(
        "/web/mu_shop/public/stylesheets/assets/review",
        "a.png",
    )
    assert key == "stylesheets/assets/review/a.png"
    assert web_path == "/stylesheets/assets/review/a.png"


def test_keeps_web_folder() -> None:
    key, web_path = public_object_path("/stylesheets/assets/popup", "banner.png")
    assert key == "stylesheets/assets/popup/banner.png"
    assert web_path == "/stylesheets/assets/popup/banner.png"
