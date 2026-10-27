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

    # 네이버 클라우드 Object Storage. 키가 비어 있으면 업로드 기능 비활성.
    ncp_access_key: str = ""
    ncp_secret_key: str = ""
    ncp_bucket: str = ""
    ncp_endpoint: str = "https://kr.object.ncloudstorage.com"
    ncp_region: str = "kr-standard"
    # 객체 키 prefix. DB에는 이 웹 경로만 저장한다.
    nas_base_path: str = "/stylesheets/assets"
    # 학습 제출(오답 노트) 이미지
    nas_review_path: str = "/stylesheets/assets/review"
    # 학습자 메인 홍보 팝업 이미지
    nas_popup_path: str = "/stylesheets/assets/popup"
    # 예전 NAS 전체 경로가 들어오면 이 prefix 를 제거하고 객체 키로 쓴다.
    nas_public_root: str = "/web/mu_shop/public"
    # 브라우저 <img> 용 공개 URL prefix. 버킷 + 웹 경로.
    nas_public_base_url: str = "https://kr.object.ncloudstorage.com"
    # 분석 완료 시 원본·overlay·result.json 자동 업로드
    nas_upload_on_analyze: bool = False
    # 의학용어 사전 CSV(팀 RAG 자료, 칸: en, kr, definition, definition_kr). 비어 있으면 쓰지 않는다.
    # 채점 해설의 "이 소견은"(정의)을 이 사전의 한글 정의로 채운다. 파일은 저장소에 넣지 않는다.
    glossary_csv_path: str = ""
    # 흉부 전용 의학용어 사전 CSV(같은 4칸). 있으면 흉부 해설은 이 사전을 먼저 보고, 없는 용어만 위 사전에서 찾는다.
    thorax_glossary_csv_path: str = ""

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
