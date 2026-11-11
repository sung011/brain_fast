-- 비밀번호 찾기 질문 Q04(내 보물 1호) 추가: 질문 ID 허용 목록만 바꾼다. 기존 답 행·다른 제약은 그대로.
-- 실행 전후에 docker/postgres/check_recovery_q04.py pre/post 로 읽기 전용 대조를 한다. 백엔드 접속 역할(표 소유자)로 실행한다.
-- 되돌리기: Q04 로 등록한 행이 없을 때만 같은 방식으로 ('Q01','Q02','Q03') 목록으로 바꿀 수 있다(별도 승인).
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE public.user_recovery_answer
    DROP CONSTRAINT user_recovery_answer_qid_check,
    ADD CONSTRAINT user_recovery_answer_qid_check CHECK (ura_question_id IN ('Q01','Q02','Q03','Q04'));

COMMIT;
