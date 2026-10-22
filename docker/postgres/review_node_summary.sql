-- review_node: 제출 당시 결과 요약.
-- 병명·정답 코드·이미지는 기존 컬럼을 쓴다.
-- 예전 행은 NULL 이고, 학습 기록에서는 포함률을 비워 둔다.

ALTER TABLE public.review_node
    ADD COLUMN IF NOT EXISTS rn_recall numeric(6, 4),
    ADD COLUMN IF NOT EXISTS rn_iou numeric(6, 4),
    ADD COLUMN IF NOT EXISTS rn_overlay_summary text,
    ADD COLUMN IF NOT EXISTS rn_findings text,
    ADD COLUMN IF NOT EXISTS rn_slice_index integer;

COMMENT ON COLUMN public.review_node.rn_recall IS '포함률. 0~1. 채점 recall';
COMMENT ON COLUMN public.review_node.rn_iou IS 'ROI 정확도. 0~1. 채점 iou';
COMMENT ON COLUMN public.review_node.rn_overlay_summary IS '이상 부위 위치 요약';
COMMENT ON COLUMN public.review_node.rn_findings IS '출혈·germinoma 소견 JSON';
COMMENT ON COLUMN public.review_node.rn_slice_index IS 'MRI 제출 슬라이스 번호(0-based). CT는 NULL';
