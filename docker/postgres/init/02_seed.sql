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
-- Data for Name: review_node; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: study; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: user_member; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.user_member (idx, del_yn, user_id, user_pw, user_name, user_birth, user_un, user_sp, created_at, updated_at, left_at, mb_level, state) OVERRIDING SYSTEM VALUE VALUES (4, 'N', 'admin', '$2b$12$r3VNru.4UObDhIPpmnL7i.cEthhCdGbA2TuDXPTP7zfAdJxdhs632', '관리자(김성일)', 's', '없음', '없음', '2026-08-26 15:13:06.387003', NULL, NULL, 10, 'N');
INSERT INTO public.user_member (idx, del_yn, user_id, user_pw, user_name, user_birth, user_un, user_sp, created_at, updated_at, left_at, mb_level, state) OVERRIDING SYSTEM VALUE VALUES (13, 'N', 'jangdoyun', 'testpw_09', '장도윤', '1991-08-27', '가톨릭대학교', '영상의학과', '2026-08-27 11:18:26.793683', NULL, NULL, 1, 'N');
INSERT INTO public.user_member (idx, del_yn, user_id, user_pw, user_name, user_birth, user_un, user_sp, created_at, updated_at, left_at, mb_level, state) OVERRIDING SYSTEM VALUE VALUES (24, 'N', 'namdohyun', 'testpw_20', '남도현', '1991-01-28', '서울대학교', '영상의학과', '2026-08-27 11:18:26.793683', NULL, NULL, 1, 'N');
INSERT INTO public.user_member (idx, del_yn, user_id, user_pw, user_name, user_birth, user_un, user_sp, created_at, updated_at, left_at, mb_level, state) OVERRIDING SYSTEM VALUE VALUES (5, 'N', 'kimminjun', 'testpw_01', '김민준', '1995-03-12', '서울대학교', '영상의학과', '2026-08-27 11:18:26.793683', NULL, NULL, 1, 'N');
INSERT INTO public.user_member (idx, del_yn, user_id, user_pw, user_name, user_birth, user_un, user_sp, created_at, updated_at, left_at, mb_level, state) OVERRIDING SYSTEM VALUE VALUES (6, 'N', 'leeseoyeon', 'testpw_02', '이서연', '1996-07-21', '연세대학교', '내과', '2026-08-27 11:18:26.793683', NULL, NULL, 1, 'N');
INSERT INTO public.user_member (idx, del_yn, user_id, user_pw, user_name, user_birth, user_un, user_sp, created_at, updated_at, left_at, mb_level, state) OVERRIDING SYSTEM VALUE VALUES (7, 'N', 'parkjiho', 'testpw_03', '박지호', '1994-11-05', '고려대학교', '외과', '2026-08-27 11:18:26.793683', NULL, NULL, 1, 'N');
INSERT INTO public.user_member (idx, del_yn, user_id, user_pw, user_name, user_birth, user_un, user_sp, created_at, updated_at, left_at, mb_level, state) OVERRIDING SYSTEM VALUE VALUES (8, 'N', 'choisua', 'testpw_04', '최수아', '1997-01-18', '성균관대학교', '신경과', '2026-08-27 11:18:26.793683', NULL, NULL, 1, 'N');
INSERT INTO public.user_member (idx, del_yn, user_id, user_pw, user_name, user_birth, user_un, user_sp, created_at, updated_at, left_at, mb_level, state) OVERRIDING SYSTEM VALUE VALUES (9, 'N', 'jungwoojin', 'testpw_05', '정우진', '1993-09-30', '한양대학교', '정형외과', '2026-08-27 11:18:26.793683', NULL, NULL, 1, 'S');
INSERT INTO public.user_member (idx, del_yn, user_id, user_pw, user_name, user_birth, user_un, user_sp, created_at, updated_at, left_at, mb_level, state) OVERRIDING SYSTEM VALUE VALUES (10, 'N', 'hanyerin', 'testpw_06', '한예린', '1998-04-02', '경희대학교', '소아과', '2026-08-27 11:18:26.793683', NULL, NULL, 1, 'N');
INSERT INTO public.user_member (idx, del_yn, user_id, user_pw, user_name, user_birth, user_un, user_sp, created_at, updated_at, left_at, mb_level, state) OVERRIDING SYSTEM VALUE VALUES (11, 'N', 'ohjunseo', 'testpw_07', '오준서', '1992-12-14', '중앙대학교', '응급의학과', '2026-08-27 11:18:26.793683', NULL, NULL, 1, 'N');
INSERT INTO public.user_member (idx, del_yn, user_id, user_pw, user_name, user_birth, user_un, user_sp, created_at, updated_at, left_at, mb_level, state) OVERRIDING SYSTEM VALUE VALUES (12, 'N', 'yunhaeun', 'testpw_08', '윤하은', '1999-06-08', '이화여자대학교', '가정의학과', '2026-08-27 11:18:26.793683', NULL, NULL, 1, 'N');
INSERT INTO public.user_member (idx, del_yn, user_id, user_pw, user_name, user_birth, user_un, user_sp, created_at, updated_at, left_at, mb_level, state) OVERRIDING SYSTEM VALUE VALUES (14, 'N', 'imseojun', 'testpw_10', '임서준', '1995-02-11', '부산대학교', '내과', '2026-08-27 11:18:26.793683', NULL, NULL, 1, 'N');
INSERT INTO public.user_member (idx, del_yn, user_id, user_pw, user_name, user_birth, user_un, user_sp, created_at, updated_at, left_at, mb_level, state) OVERRIDING SYSTEM VALUE VALUES (15, 'N', 'shinjiia', 'testpw_11', '신지아', '1996-10-03', '서울대학교', '방사선종양', '2026-08-27 11:18:26.793683', NULL, NULL, 1, 'N');
INSERT INTO public.user_member (idx, del_yn, user_id, user_pw, user_name, user_birth, user_un, user_sp, created_at, updated_at, left_at, mb_level, state) OVERRIDING SYSTEM VALUE VALUES (16, 'N', 'kanghajun', 'testpw_12', '강하준', '1994-05-19', '연세대학교', '핵의학과', '2026-08-27 11:18:26.793683', NULL, NULL, 1, 'N');
INSERT INTO public.user_member (idx, del_yn, user_id, user_pw, user_name, user_birth, user_un, user_sp, created_at, updated_at, left_at, mb_level, state) OVERRIDING SYSTEM VALUE VALUES (17, 'N', 'jominseo', 'testpw_13', '조민서', '1997-03-25', '고려대학교', '영상의학과', '2026-08-27 11:18:26.793683', NULL, NULL, 1, 'N');
INSERT INTO public.user_member (idx, del_yn, user_id, user_pw, user_name, user_birth, user_un, user_sp, created_at, updated_at, left_at, mb_level, state) OVERRIDING SYSTEM VALUE VALUES (18, 'N', 'baesuhyun', 'testpw_14', '배수현', '1993-07-07', '성균관대학교', '외과', '2026-08-27 11:18:26.793683', NULL, NULL, 1, 'N');
INSERT INTO public.user_member (idx, del_yn, user_id, user_pw, user_name, user_birth, user_un, user_sp, created_at, updated_at, left_at, mb_level, state) OVERRIDING SYSTEM VALUE VALUES (19, 'Y', 'moonchaewon', 'testpw_15', '문채원', '1998-09-16', '한양대학교', '신경과', '2026-08-27 11:18:26.793683', '2026-01-10 09:00:00', '2026-01-10 09:00:00', 1, 'S');
INSERT INTO public.user_member (idx, del_yn, user_id, user_pw, user_name, user_birth, user_un, user_sp, created_at, updated_at, left_at, mb_level, state) OVERRIDING SYSTEM VALUE VALUES (20, 'N', 'kwonjihu', 'testpw_16', '권지후', '1990-12-01', '경희대학교', '정형외과', '2026-08-27 11:18:26.793683', NULL, NULL, 1, 'N');
INSERT INTO public.user_member (idx, del_yn, user_id, user_pw, user_name, user_birth, user_un, user_sp, created_at, updated_at, left_at, mb_level, state) OVERRIDING SYSTEM VALUE VALUES (21, 'N', 'songarin', 'testpw_17', '송아린', '1999-02-22', '중앙대학교', '소아과', '2026-08-27 11:18:26.793683', NULL, NULL, 1, 'N');
INSERT INTO public.user_member (idx, del_yn, user_id, user_pw, user_name, user_birth, user_un, user_sp, created_at, updated_at, left_at, mb_level, state) OVERRIDING SYSTEM VALUE VALUES (22, 'N', 'hongsieu', 'testpw_18', '홍시우', '1992-04-09', '부산대학교', '응급의학과', '2026-08-27 11:18:26.793683', NULL, NULL, 1, 'N');
INSERT INTO public.user_member (idx, del_yn, user_id, user_pw, user_name, user_birth, user_un, user_sp, created_at, updated_at, left_at, mb_level, state) OVERRIDING SYSTEM VALUE VALUES (23, 'N', 'baekharin', 'testpw_19', '백하린', '1996-08-13', '가톨릭대학교', '가정의학과', '2026-08-27 11:18:26.793683', NULL, NULL, 1, 'N');


--
-- Name: review_node_idx_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.review_node_idx_seq', 1, false);


--
-- Name: study_idx_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.study_idx_seq', 1, false);


--
-- Name: user_member_idx_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.user_member_idx_seq', 24, true);


--
-- PostgreSQL database dump complete
--


