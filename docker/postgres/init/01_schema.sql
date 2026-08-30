--
-- PostgreSQL database dump
--


-- Dumped from database version 15.18 (Homebrew)
-- Dumped by pg_dump version 15.18 (Homebrew)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--



--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--



SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: review_node; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.review_node (
    idx integer NOT NULL,
    del_yn character(1) DEFAULT 'N'::bpchar NOT NULL,
    rn_u_idx integer,
    rn_s_idx integer,
    rn_disease character varying(30),
    rn_image text,
    rn_solution character(1),
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at character varying(30),
    left_at character varying(30),
    state character(1) DEFAULT 'N'::bpchar NOT NULL,
    CONSTRAINT review_node_del_yn_check CHECK ((del_yn = ANY (ARRAY['Y'::bpchar, 'N'::bpchar]))),
    CONSTRAINT review_node_rn_solution_check CHECK (((rn_solution IS NULL) OR (rn_solution = ANY (ARRAY['C'::bpchar, 'H'::bpchar, 'W'::bpchar])))),
    CONSTRAINT review_node_state_check CHECK ((state = ANY (ARRAY['N'::bpchar, 'S'::bpchar])))
);


--
-- Name: TABLE review_node; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.review_node IS '오답 노트';


--
-- Name: COLUMN review_node.idx; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.review_node.idx IS '인덱스';


--
-- Name: COLUMN review_node.del_yn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.review_node.del_yn IS 'Y: 삭제 / N: 삭제안함';


--
-- Name: COLUMN review_node.rn_u_idx; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.review_node.rn_u_idx IS '회원_idx';


--
-- Name: COLUMN review_node.rn_s_idx; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.review_node.rn_s_idx IS '학습_idx';


--
-- Name: COLUMN review_node.rn_disease; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.review_node.rn_disease IS '병명';


--
-- Name: COLUMN review_node.rn_image; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.review_node.rn_image IS '이미지 경로';


--
-- Name: COLUMN review_node.rn_solution; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.review_node.rn_solution IS '정답:C / 부분정답:H / 오답:W';


--
-- Name: COLUMN review_node.created_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.review_node.created_at IS '등록일자';


--
-- Name: COLUMN review_node.updated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.review_node.updated_at IS '수정일자';


--
-- Name: COLUMN review_node.left_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.review_node.left_at IS '탈퇴일자';


--
-- Name: COLUMN review_node.state; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.review_node.state IS '정상:N / 탈퇴:S';


--
-- Name: review_node_idx_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.review_node ALTER COLUMN idx ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.review_node_idx_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: study; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.study (
    idx integer NOT NULL,
    del_yn character(1) DEFAULT 'N'::bpchar NOT NULL,
    st_part character(1),
    st_modal character(1),
    st_image text,
    st_disease character varying(30),
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at character varying(30),
    left_at character varying(30),
    state character(1) DEFAULT 'N'::bpchar NOT NULL,
    CONSTRAINT study_del_yn_check CHECK ((del_yn = ANY (ARRAY['Y'::bpchar, 'N'::bpchar]))),
    CONSTRAINT study_st_modal_check CHECK (((st_modal IS NULL) OR (st_modal = ANY (ARRAY['1'::bpchar, '2'::bpchar, '3'::bpchar])))),
    CONSTRAINT study_st_part_check CHECK (((st_part IS NULL) OR (st_part = ANY (ARRAY['1'::bpchar, '2'::bpchar, '3'::bpchar, '4'::bpchar])))),
    CONSTRAINT study_state_check CHECK ((state = ANY (ARRAY['N'::bpchar, 'S'::bpchar])))
);


--
-- Name: TABLE study; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.study IS '학습';


--
-- Name: COLUMN study.idx; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.study.idx IS '인덱스';


--
-- Name: COLUMN study.del_yn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.study.del_yn IS 'Y: 삭제 / N: 삭제안함';


--
-- Name: COLUMN study.st_part; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.study.st_part IS '1:뇌 / 2:흉부 / 3:복부 / 4:무릎';


--
-- Name: COLUMN study.st_modal; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.study.st_modal IS '1:X-ray / 2:CT / 3:MRI';


--
-- Name: COLUMN study.st_image; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.study.st_image IS '이미지 경로';


--
-- Name: COLUMN study.st_disease; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.study.st_disease IS '병명';


--
-- Name: COLUMN study.created_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.study.created_at IS '등록일자';


--
-- Name: COLUMN study.updated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.study.updated_at IS '수정일자';


--
-- Name: COLUMN study.left_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.study.left_at IS '탈퇴일자';


--
-- Name: COLUMN study.state; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.study.state IS '정상:N / 탈퇴:S';


--
-- Name: study_idx_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.study ALTER COLUMN idx ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.study_idx_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: user_member; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_member (
    idx integer NOT NULL,
    del_yn character(1) DEFAULT 'N'::bpchar NOT NULL,
    user_id character varying(30) NOT NULL,
    user_pw character varying(255) NOT NULL,
    user_name character varying(10),
    user_birth character varying(10) NOT NULL,
    user_un character varying(20) NOT NULL,
    user_sp character varying(20) NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at character varying(30),
    left_at character varying(30),
    mb_level integer DEFAULT 1 NOT NULL,
    state character(1) DEFAULT 'N'::bpchar NOT NULL,
    CONSTRAINT user_member_del_yn_check CHECK ((del_yn = ANY (ARRAY['Y'::bpchar, 'N'::bpchar]))),
    CONSTRAINT user_member_state_check CHECK ((state = ANY (ARRAY['N'::bpchar, 'S'::bpchar, 'W'::bpchar])))
);


--
-- Name: TABLE user_member; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.user_member IS '회원';


--
-- Name: COLUMN user_member.idx; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_member.idx IS '인덱스';


--
-- Name: COLUMN user_member.del_yn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_member.del_yn IS 'Y: 삭제 / N: 삭제안함';


--
-- Name: COLUMN user_member.user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_member.user_id IS '아이디&이메일';


--
-- Name: COLUMN user_member.user_pw; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_member.user_pw IS '패스워드';


--
-- Name: COLUMN user_member.user_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_member.user_name IS '회원 이름 & 닉네임 (최대 5자리)';


--
-- Name: COLUMN user_member.user_birth; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_member.user_birth IS '생년월일';


--
-- Name: COLUMN user_member.user_un; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_member.user_un IS '학교';


--
-- Name: COLUMN user_member.user_sp; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_member.user_sp IS '전공';


--
-- Name: COLUMN user_member.created_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_member.created_at IS '등록일자';


--
-- Name: COLUMN user_member.updated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_member.updated_at IS '수정일자';


--
-- Name: COLUMN user_member.left_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_member.left_at IS '탈퇴일자';


--
-- Name: COLUMN user_member.mb_level; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_member.mb_level IS '10: 최고관리자mb_level / 1: 일반';


--
-- Name: COLUMN user_member.state; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_member.state IS '정상:N / 탈퇴:S';


--
-- Name: user_member_idx_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.user_member ALTER COLUMN idx ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.user_member_idx_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: review_node review_node_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.review_node
    ADD CONSTRAINT review_node_pkey PRIMARY KEY (idx);


--
-- Name: study study_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.study
    ADD CONSTRAINT study_pkey PRIMARY KEY (idx);


--
-- Name: user_member user_member_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_member
    ADD CONSTRAINT user_member_pkey PRIMARY KEY (idx);


--
-- Name: user_member user_member_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_member
    ADD CONSTRAINT user_member_user_id_key UNIQUE (user_id);


--
-- Name: idx_review_node_study; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_review_node_study ON public.review_node USING btree (rn_s_idx);


--
-- Name: idx_review_node_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_review_node_user ON public.review_node USING btree (rn_u_idx);


--
-- Name: idx_study_part_modal; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_study_part_modal ON public.study USING btree (st_part, st_modal);


--
-- PostgreSQL database dump complete
--


