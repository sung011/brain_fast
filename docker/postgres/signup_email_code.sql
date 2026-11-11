-- 팀 공유 DB 적용 전에 기존 user_id 타입과 의존 객체를 확인한다.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE public.user_member ALTER COLUMN user_id TYPE varchar(254);

CREATE TABLE public.signup_email_code (
    email_key varchar(64) PRIMARY KEY,
    code_digest varchar(64) NOT NULL,
    sent_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    failed_attempts integer NOT NULL CHECK (failed_attempts BETWEEN 0 AND 5),
    CHECK (sent_at < expires_at)
);

CREATE INDEX signup_email_code_expires_idx ON public.signup_email_code (expires_at);
ALTER TABLE public.signup_email_code ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.signup_email_code FROM PUBLIC;

DO $$
DECLARE
    api_role text;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('REVOKE ALL ON TABLE public.signup_email_code FROM %I', api_role);
        END IF;
    END LOOP;
END
$$;

COMMIT;

