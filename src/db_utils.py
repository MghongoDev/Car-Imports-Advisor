import os
import pandas as pd
from sqlalchemy import create_engine

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "raw_cars.db")

def init_db():
    """Initialize database connection"""
    engine = create_engine(f'sqlite:///{DB_PATH}')
    return engine

def store_data(df, table_name='car_listings'):
    """Store dataframe into SQLite"""
    engine = init_db()
    df.to_sql(table_name, engine, if_exists='replace', index=False)
    print(f"Data stored successfully in {DB_PATH}")

def load_data(table_name='car_listings'):
    """Load data from SQLite"""
    engine = init_db()
    try:
        df = pd.read_sql(f"SELECT * FROM {table_name}", engine)
        return df
    except:
        return pd.DataFrame()
