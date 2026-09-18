-- Q&A 문의 스레드 (회원 ↔ 관리자)
CREATE TABLE IF NOT EXISTS public.qa_thread (
    idx integer NOT NULL GENERATED ALWAYS AS IDENTITY,
    del_yn character(1) DEFAULT 'N'::bpchar NOT NULL,
    qt_u_idx integer,
    qt_title character varying(100),
    qt_last_msg text,
    qt_last_at timestamp without time zone,
    qt_unread_admin integer DEFAULT 0 NOT NULL,
    qt_unread_user integer DEFAULT 0 NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at character varying(30),
    left_at character varying(30),
    state character(1) DEFAULT 'N'::bpchar NOT NULL,
    CONSTRAINT qa_thread_pkey PRIMARY KEY (idx),
    CONSTRAINT qa_thread_del_yn_check CHECK ((del_yn = ANY (ARRAY['Y'::bpchar, 'N'::bpchar]))),
    CONSTRAINT qa_thread_state_check CHECK ((state = ANY (ARRAY['N'::bpchar, 'S'::bpchar])))
);

COMMENT ON TABLE public.qa_thread IS 'Q&A 문의 스레드';
COMMENT ON COLUMN public.qa_thread.qt_u_idx IS '문의 회원 user_member.idx';
COMMENT ON COLUMN public.qa_thread.qt_title IS '문의 제목';
COMMENT ON COLUMN public.qa_thread.qt_last_msg IS '최근 메시지 미리보기';
COMMENT ON COLUMN public.qa_thread.qt_last_at IS '최근 메시지 시각';
COMMENT ON COLUMN public.qa_thread.qt_unread_admin IS '관리자 미읽음 수';
COMMENT ON COLUMN public.qa_thread.qt_unread_user IS '회원 미읽음 수';
COMMENT ON COLUMN public.qa_thread.state IS 'N:진행중 / S:종료';

CREATE INDEX IF NOT EXISTS idx_qa_thread_user ON public.qa_thread USING btree (qt_u_idx);
CREATE INDEX IF NOT EXISTS idx_qa_thread_last ON public.qa_thread USING btree (del_yn, qt_last_at DESC);

-- Q&A 메시지
CREATE TABLE IF NOT EXISTS public.qa_message (
    idx integer NOT NULL GENERATED ALWAYS AS IDENTITY,
    del_yn character(1) DEFAULT 'N'::bpchar NOT NULL,
    qm_t_idx integer NOT NULL,
    qm_u_idx integer,
    qm_role character(1) NOT NULL,
    qm_body text NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at character varying(30),
    left_at character varying(30),
    state character(1) DEFAULT 'N'::bpchar NOT NULL,
    CONSTRAINT qa_message_pkey PRIMARY KEY (idx),
    CONSTRAINT qa_message_del_yn_check CHECK ((del_yn = ANY (ARRAY['Y'::bpchar, 'N'::bpchar]))),
    CONSTRAINT qa_message_role_check CHECK ((qm_role = ANY (ARRAY['U'::bpchar, 'A'::bpchar]))),
    CONSTRAINT qa_message_state_check CHECK ((state = ANY (ARRAY['N'::bpchar, 'S'::bpchar])))
);

COMMENT ON TABLE public.qa_message IS 'Q&A 메시지';
COMMENT ON COLUMN public.qa_message.qm_t_idx IS 'qa_thread.idx';
COMMENT ON COLUMN public.qa_message.qm_u_idx IS '발신자 user_member.idx';
COMMENT ON COLUMN public.qa_message.qm_role IS 'U:회원 / A:관리자';
COMMENT ON COLUMN public.qa_message.qm_body IS '메시지 본문';
COMMENT ON COLUMN public.qa_message.state IS 'N:정상 / S:숨김';

CREATE INDEX IF NOT EXISTS idx_qa_message_thread ON public.qa_message USING btree (qm_t_idx, created_at);
