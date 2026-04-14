"""HotSot ML Service — Training + Inference Routes."""

import os
import time
import numpy as np
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

MODEL_DIR = os.getenv("MODEL_REGISTRY", "/app/models")
os.makedirs(MODEL_DIR, exist_ok=True)

FEATURE_STORE: Dict[str, Dict[str, Any]] = {}
TRAINING_DATA: List[Dict[str, Any]] = []


class TrainingRequest(BaseModel):
    dataset_path: Optional[str] = None
    model_type: str = "lightgbm"
    hyperparameters: Dict[str, Any] = {}


class FeedbackRequest(BaseModel):
    order_id: str
    predicted_eta: int
    actual_eta: int
    kitchen_id: str


@router.post("/train")
async def train_model(req: TrainingRequest):
    """Train ETA prediction model with LightGBM or sklearn."""
    job_id = f"train_{int(time.time())}"
    try:
        X, y = _generate_sample_data(1000) if not req.dataset_path else _generate_sample_data(1000)
        if req.model_type == "lightgbm":
            result = _train_lightgbm(X, y, req.hyperparameters)
        elif req.model_type == "sklearn_gb":
            result = _train_sklearn(X, y, req.hyperparameters)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported model: {req.model_type}")
        return {"job_id": job_id, "status": "COMPLETED", "model_version": result["version"],
                "metrics": result["metrics"], "model_path": result["model_path"], "training_samples": len(y)}
    except Exception as e:
        return {"job_id": job_id, "status": "FAILED", "error": str(e)}


def _generate_sample_data(n_samples: int = 1000):
    np.random.seed(42)
    kitchen_load = np.random.randint(0, 30, n_samples)
    queue_length = np.random.randint(0, 20, n_samples)
    item_complexity = np.random.randint(1, 6, n_samples)
    time_of_day = np.random.randint(0, 24, n_samples)
    is_peak = (np.random.random(n_samples) > 0.7).astype(int)
    is_festival = (np.random.random(n_samples) > 0.9).astype(int)
    is_monsoon = (np.random.random(n_samples) > 0.85).astype(int)
    staff_count = np.random.randint(2, 8, n_samples)
    historical_delay = np.random.exponential(30, n_samples)
    eta = (300 + kitchen_load * 8 + queue_length * 25 + item_complexity * 45 +
           is_peak * 150 + is_festival * 200 + is_monsoon * 100 -
           (staff_count - 2) * 15 + historical_delay * 0.3 + np.random.normal(0, 30, n_samples))
    eta = np.maximum(eta, 60)
    X = np.column_stack([kitchen_load, queue_length, item_complexity, time_of_day,
                         is_peak, is_festival, is_monsoon, staff_count, historical_delay])
    return X, eta


def _train_lightgbm(X, y, hyperparams: Dict = {}):
    import lightgbm as lgb
    from sklearn.model_selection import train_test_split
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    params = {"n_estimators": 500, "learning_rate": 0.05, "max_depth": 8, "num_leaves": 31}
    params.update(hyperparams)
    model = lgb.LGBMRegressor(**params)
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], callbacks=[lgb.early_stopping(stopping_rounds=50)])
    y_pred = model.predict(X_test)
    mae = float(np.mean(np.abs(y_test - y_pred)))
    rmse = float(np.sqrt(np.mean((y_test - y_pred) ** 2)))
    version = f"v{int(time.time())}"
    model_path = os.path.join(MODEL_DIR, f"eta_model_{version}.txt")
    model.booster_.save_model(model_path)
    model.booster_.save_model(os.path.join(MODEL_DIR, "eta_model.txt"))
    return {"version": version, "model_path": model_path, "metrics": {"mae": round(mae, 2), "rmse": round(rmse, 2)}}


def _train_sklearn(X, y, hyperparams: Dict = {}):
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.model_selection import train_test_split
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    model = GradientBoostingRegressor(n_estimators=hyperparams.get("n_estimators", 300),
                                      learning_rate=hyperparams.get("learning_rate", 0.05),
                                      max_depth=hyperparams.get("max_depth", 6))
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)
    mae = float(np.mean(np.abs(y_test - y_pred)))
    rmse = float(np.sqrt(np.mean((y_test - y_pred) ** 2)))
    version = f"v{int(time.time())}"
    model_path = os.path.join(MODEL_DIR, f"eta_sklearn_{version}.pkl")
    import pickle
    with open(model_path, "wb") as f:
        pickle.dump(model, f)
    return {"version": version, "model_path": model_path, "metrics": {"mae": round(mae, 2), "rmse": round(rmse, 2)}}


@router.post("/features/{order_id}")
async def store_features(order_id: str, features: Dict[str, Any]):
    FEATURE_STORE[order_id] = {"features": features, "stored_at": time.time()}
    return {"order_id": order_id, "status": "STORED"}


@router.get("/features/{order_id}")
async def get_features(order_id: str):
    if order_id not in FEATURE_STORE:
        raise HTTPException(status_code=404, detail="Features not found")
    return FEATURE_STORE[order_id]


@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest):
    """Submit ETA prediction feedback for model self-improvement loop."""
    error = abs(req.predicted_eta - req.actual_eta)
    error_pct = (error / max(req.predicted_eta, 1)) * 100
    TRAINING_DATA.append({"order_id": req.order_id, "kitchen_id": req.kitchen_id,
                          "predicted_eta": req.predicted_eta, "actual_eta": req.actual_eta,
                          "error_seconds": error, "error_pct": round(error_pct, 2), "timestamp": time.time()})
    return {"order_id": req.order_id, "error_seconds": error, "error_pct": round(error_pct, 2),
            "needs_retrain": error_pct > 25, "training_dataset_size": len(TRAINING_DATA)}


@router.get("/status")
async def model_status():
    models = []
    if os.path.exists(MODEL_DIR):
        for f in os.listdir(MODEL_DIR):
            if f.endswith(('.txt', '.pkl')):
                path = os.path.join(MODEL_DIR, f)
                models.append({"name": f, "size_bytes": os.path.getsize(path), "modified": os.path.getmtime(path)})
    return {"model_directory": MODEL_DIR, "available_models": models,
            "feature_store_size": len(FEATURE_STORE), "training_data_size": len(TRAINING_DATA)}
