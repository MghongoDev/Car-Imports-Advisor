import os
import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import OneHotEncoder
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.metrics import mean_absolute_error, r2_score
import joblib

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
MODEL_DIR = os.path.join(BASE_DIR, "models")
os.makedirs(MODEL_DIR, exist_ok=True)
MODEL_PATH = os.path.join(MODEL_DIR, "car_price_model.pkl")

def model_exists():
    return os.path.exists(MODEL_PATH)


def train_and_save_model(df):
    """
    Trains a regression model to predict car prices based on specs.
    """
    # Features
    X = df[['make', 'model', 'year', 'mileage_km', 'engine_cc', 'fuel_type', 'transmission', 'body_type']]
    y = df['price_jpy']
    
    # Preprocessing: OneHotEncode categorical variables
    categorical_features = ['make', 'model', 'fuel_type', 'transmission', 'body_type']
    numerical_features = ['year', 'mileage_km', 'engine_cc']
    
    preprocessor = ColumnTransformer(
        transformers=[
            ('cat', OneHotEncoder(handle_unknown='ignore'), categorical_features),
            ('num', 'passthrough', numerical_features)
        ])
    
    # Pipeline
    model = Pipeline(steps=[
        ('preprocessor', preprocessor),
        ('regressor', RandomForestRegressor(n_estimators=100, random_state=42))
    ])
    
    # Train/Test Split
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    # Train
    model.fit(X_train, y_train)
    
    # Evaluate
    preds = model.predict(X_test)
    print(f"Model MAE: {mean_absolute_error(y_test, preds):.2f} JPY")
    print(f"Model R2: {r2_score(y_test, preds):.2f}")
    
    # Save
    joblib.dump(model, MODEL_PATH)
    print(f"Model saved to {MODEL_PATH}")
    return model

def load_model():
    try:
        return joblib.load(MODEL_PATH)
    except FileNotFoundError:
        return None
    except Exception:
        return None

def predict_price(model, input_data):
    """
    input_data: Dictionary matching feature columns
    """
    df = pd.DataFrame([input_data])
    prediction = model.predict(df)
    return prediction[0]
