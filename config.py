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
