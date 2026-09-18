"""
앱 설정.

환경 변수와 .env 파일에서 값을 읽는다.
DB 주소는 DATABASE_URL 로 넘긴다.
"""

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class Settings(BaseSettings):
    """실행 설정. 환경 변수가 .env 파일보다 우선한다."""

    database_url: str = (
        "postgresql+psycopg://postgres:1234@127.0.0.1:5432/mediscan_note"
    )
    # 세션 쿠키 서명용. 운영에서는 .env 의 SECRET_KEY 로 바꾼다.
    secret_key: str = "dev-secret-change-me"

    # Synology NAS (File Station). 비어 있으면 업로드 기능 비활성.
    nas_url: str = ""
    nas_user: str = ""
    nas_password: str = ""
    # File Station/웹 상대 경로 기본값 (실제 NAS 업로드 시 nas_public_root 를 앞에 붙임)
    nas_base_path: str = "/stylesheets/assets"
    # 학습 제출(오답 노트) 이미지. NAS에는 /web/mu_shop/public/stylesheets/assets/review
    nas_review_path: str = "/stylesheets/assets/review"
    # DB st_image 저장 시 이 prefix 를 제거한다. (/web/mu_shop/public + /stylesheets/...)
    nas_public_root: str = "/web/mu_shop/public"
    # 브라우저에서 이미지 볼 때 쓰는 공개 URL prefix
    # 예) https://host/mu_shop/public + /stylesheets/assets/...
    nas_public_base_url: str = "https://olleh7531.synology.me/mu_shop/public"
    nas_verify_ssl: bool = True
    nas_timeout: float = 60.0
    # 분석 완료 시 원본·overlay·result.json 자동 업로드
    nas_upload_on_analyze: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Docker environment 가 .env 의 127.0.0.1 을 덮어쓰게 한다.
        return (init_settings, env_settings, dotenv_settings, file_secret_settings)


settings = Settings()
