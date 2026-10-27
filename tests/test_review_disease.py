"""제출 리뷰의 rn_disease는 확률이 가장 높은 병명이다."""

from __future__ import annotations

import unittest

from services.reviewNodeServices import highest_percent_disease


class HighestPercentDiseaseTests(unittest.TestCase):
    def test_picks_highest_hemorrhage_label(self) -> None:
        label = highest_percent_disease(
            {
                "classification": {
                    "top_label_ko": "경막하출혈",
                    "findings": [
                        {"label": "subdural", "label_ko": "경막하출혈", "score": 0.21},
                        {"label": "epidural", "label_ko": "경막외출혈", "score": 0.74},
                    ],
                }
            }
        )
        self.assertEqual(label, "경막외출혈")

    def test_germinoma_wins_when_its_score_is_higher(self) -> None:
        label = highest_percent_disease(
            {
                "classification": {
                    "findings": [
                        {"label": "any", "label_ko": "두개내출혈", "score": 0.4},
                    ],
                },
                "germinoma": {
                    "top_label_ko": "생식세포종양",
                    "confidence": 0.91,
                    "findings": [
                        {"label": "tumor", "label_ko": "생식세포종양", "score": 0.91},
                    ],
                },
            }
        )
        self.assertEqual(label, "생식세포종양")

    def test_falls_back_to_top_label_without_findings(self) -> None:
        label = highest_percent_disease(
            {"classification": {"top_label_ko": "지주막하출혈"}}
        )
        self.assertEqual(label, "지주막하출혈")
