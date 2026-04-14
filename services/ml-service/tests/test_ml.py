"""HotSot ML Service — Unit Tests."""
import pytest

def test_feature_store_init():
    from ml.feature_store.store import FeatureStore
    fs = FeatureStore()
    assert fs is not None

def test_training_features():
    from ml.training.features import FeatureExtractor
    fe = FeatureExtractor()
    assert fe is not None

def test_dataset_builder():
    from ml.training.dataset_builder import DatasetBuilder
    db = DatasetBuilder()
    assert db is not None
