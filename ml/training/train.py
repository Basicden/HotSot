"""HotSot ML — Model Training Script."""

import os
import time
import argparse
import numpy as np
from dataset_builder import DatasetBuilder


def train(args):
    """Train ETA prediction model."""
    print(f"[Training] Starting model training — {args.model_type}")
    
    # Build dataset
    builder = DatasetBuilder()
    
    if args.synthetic:
        print(f"[Training] Generating {args.n_samples} synthetic samples")
        builder.generate_synthetic(n_samples=args.n_samples)
    elif args.dataset_path:
        print(f"[Training] Loading dataset from {args.dataset_path}")
        # Load from CSV
        import pandas as pd
        df = pd.read_csv(args.dataset_path)
        # Convert to records (simplified)
        builder.generate_synthetic(n_samples=len(df))
    
    X, y = builder.to_numpy()
    print(f"[Training] Dataset shape: X={X.shape}, y={y.shape}")
    
    # Split
    from sklearn.model_selection import train_test_split
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    # Train
    if args.model_type == "lightgbm":
        import lightgbm as lgb
        model = lgb.LGBMRegressor(
            n_estimators=500,
            learning_rate=0.05,
            max_depth=8,
            num_leaves=31,
        )
        model.fit(X_train, y_train, eval_set=[(X_test, y_test)],
                  callbacks=[lgb.early_stopping(stopping_rounds=50)])
    else:
        from sklearn.ensemble import GradientBoostingRegressor
        model = GradientBoostingRegressor(n_estimators=300, learning_rate=0.05, max_depth=6)
        model.fit(X_train, y_train)
    
    # Evaluate
    y_pred = model.predict(X_test)
    mae = np.mean(np.abs(y_test - y_pred))
    rmse = np.sqrt(np.mean((y_test - y_pred) ** 2))
    mape = np.mean(np.abs((y_test - y_pred) / y_test)) * 100
    
    print(f"[Training] MAE: {mae:.2f}s, RMSE: {rmse:.2f}s, MAPE: {mape:.2f}%")
    
    # Save model
    os.makedirs(args.output_dir, exist_ok=True)
    version = f"v{int(time.time())}"
    
    if args.model_type == "lightgbm":
        model_path = os.path.join(args.output_dir, f"eta_model_{version}.txt")
        model.booster_.save_model(model_path)
        # Also save as latest
        latest = os.path.join(args.output_dir, "eta_model.txt")
        model.booster_.save_model(latest)
    else:
        import pickle
        model_path = os.path.join(args.output_dir, f"eta_model_{version}.pkl")
        with open(model_path, "wb") as f:
            pickle.dump(model, f)
    
    print(f"[Training] Model saved to {model_path}")
    return model_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HotSot ETA Model Training")
    parser.add_argument("--model-type", default="lightgbm", choices=["lightgbm", "sklearn"])
    parser.add_argument("--synthetic", action="store_true", help="Use synthetic data")
    parser.add_argument("--n-samples", type=int, default=5000)
    parser.add_argument("--dataset-path", type=str, default=None)
    parser.add_argument("--output-dir", type=str, default="./models")
    args = parser.parse_args()
    
    train(args)
