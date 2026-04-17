"""HotSot Kitchen Service — Test Suite."""

import pytest
from app.core.engine import PriorityScoreCalculator, QueueManager, BatchEngine


class TestPriorityScoreCalculator:
    """Tests for the priority score calculator."""

    def test_free_tier_base_score(self):
        score = PriorityScoreCalculator.calculate(user_tier="FREE")
        assert score >= 5.0  # Minimum tier bonus
        assert score <= 200.0

    def test_vip_tier_higher_than_free(self):
        free_score = PriorityScoreCalculator.calculate(user_tier="FREE")
        vip_score = PriorityScoreCalculator.calculate(user_tier="VIP")
        assert vip_score > free_score

    def test_arrival_boost(self):
        no_boost = PriorityScoreCalculator.calculate(user_tier="FREE", arrival_boost=0)
        with_boost = PriorityScoreCalculator.calculate(user_tier="FREE", arrival_boost=20)
        assert with_boost > no_boost

    def test_queue_type_determination(self):
        assert PriorityScoreCalculator.determine_queue_type(90) == "IMMEDIATE"
        assert PriorityScoreCalculator.determine_queue_type(60) == "NORMAL"
        assert PriorityScoreCalculator.determine_queue_type(30) == "BATCH"

    def test_batch_category_from_items(self):
        items = [{"name": "biryani"}, {"name": "raita"}]
        category = PriorityScoreCalculator.determine_batch_category(items)
        assert category == "RICE_BOWL"

    def test_batch_category_empty_items(self):
        category = PriorityScoreCalculator.determine_batch_category([])
        assert category is None

    def test_score_max_cap(self):
        score = PriorityScoreCalculator.calculate(
            user_tier="VIP",
            arrival_proximity=0,
            delay_risk=1.0,
            order_age_seconds=3600,
            arrival_boost=50,
        )
        assert score <= 200.0
