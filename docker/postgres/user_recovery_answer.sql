-- 비밀번호 찾기 질문 답변(복구 B안, security_questions_v1 / answer_norm_v1)
--
-- 주의
-- - 이 파일은 **자동 실행되지 않는다**(init/ 폴더 밖, review_node_summary.sql 과 같은 수동 적용 파일).
--   init/ 에는 user_member 를 만드는 파일이 없어서, 이 DDL 을 init/ 에 두면 새 데이터 디렉터리 초기화가 FK 오류로 멈춘다.
--   init/ 에 넣을지는 user_member DDL 정비와 함께 팀이 정한다(T6).
-- - 공유 DB 에 적용할 때는 사전 스키마 대조(ABSENT/MATCH/MISMATCH) 결과가 ABSENT 일 때만, 팀이 정한 실행자가 이 내용을 실행한다.
--   MISMATCH 면 중단한다(자동 ALTER·덮어쓰기 없음).
-- - IF NOT EXISTS 를 쓰지 않는다. 이미 있으면 오류로 멈추고 사전 대조로 판단한다.
-- - 기존 테이블(user_member 등)·전역 기본 권한은 바꾸지 않는다. FK ON DELETE RESTRICT 는 잠정안(팀 합의 T2).
-- - 답 평문은 저장하지 않는다(정규화 후 bcrypt 해시만).
-- - [R-D 보안, RECOVERY_B_RD_RLS_DESIGN_ACCEPTANCE.md] 백엔드(테이블 소유 역할)만 쓰는 서버 내부 표다.
--   같은 트랜잭션 안에서 RLS 를 켜고(정책 0개, FORCE 없음) PUBLIC·anon·authenticated 의 표·identity 시퀀스 권한을 회수한다.
--   공개 전에 보안 설정까지 끝나도록 한 BEGIN/COMMIT 이다. 실행 역할 = 백엔드가 접속하는 역할(새 표의 소유자)이어야 한다.
--   anon·authenticated 역할은 Supabase 에만 있다(없는 일반 PostgreSQL 에서는 건너뛴다). 역할·객체 이름은 상수만 쓴다.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
CREATE TABLE public.user_recovery_answer (
    idx integer NOT NULL GENERATED ALWAYS AS IDENTITY,
    ura_u_idx integer NOT NULL,
    ura_question_id character varying(8) NOT NULL,
    ura_answer_hash character varying(255) NOT NULL,
    ura_question_set character varying(40) DEFAULT 'security_questions_v1' NOT NULL,
    ura_norm_version character varying(20) DEFAULT 'answer_norm_v1' NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at character varying(30),
    CONSTRAINT user_recovery_answer_pkey PRIMARY KEY (idx),
    CONSTRAINT user_recovery_answer_user_fk FOREIGN KEY (ura_u_idx) REFERENCES public.user_member(idx) ON DELETE RESTRICT,
    CONSTRAINT user_recovery_answer_qid_check CHECK (ura_question_id IN ('Q01','Q02','Q03','Q04')),
    CONSTRAINT user_recovery_answer_user_question_uq UNIQUE (ura_u_idx, ura_question_id)
);
COMMENT ON TABLE public.user_recovery_answer IS '비밀번호 찾기 질문 답변(정규화 후 bcrypt 해시만, 평문 없음)';
ALTER TABLE public.user_recovery_answer ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public.user_recovery_answer FROM PUBLIC;
DO $$
DECLARE
    seq regclass := pg_get_serial_sequence('public.user_recovery_answer', 'idx')::regclass;
    api_role text;
BEGIN
    IF seq IS NULL THEN
        RAISE EXCEPTION 'user_recovery_answer identity sequence not found';
    END IF;
    EXECUTE format('REVOKE ALL PRIVILEGES ON SEQUENCE %s FROM PUBLIC', seq);
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.user_recovery_answer FROM %I', api_role);
            EXECUTE format('REVOKE ALL PRIVILEGES ON SEQUENCE %s FROM %I', seq, api_role);
        END IF;
    END LOOP;
END
$$;
COMMIT;
