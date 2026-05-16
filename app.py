import streamlit as st
import pandas as pd
import sys
import os

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from src.extraction import get_data, generate_mock_data
from src.db_utils import store_data, load_data, init_db
from src.calculator import calculate_import_cost
from src.ml_model import train_and_save_model, load_model, predict_price, model_exists

# --- Page Config ---
st.set_page_config(page_title="Japan Car Import Advisor", layout="wide")

st.title("🚗 Japan Car Import Advisory Platform (Kenya)")
st.markdown("Compare import costs vs local prices and predict car values from Japan.")

# --- Sidebar Actions ---
st.sidebar.header("System Actions")

if model_exists():
    st.sidebar.success("Model file found — prediction available.")
else:
    st.sidebar.info("Model not trained yet. Click '2. Train ML Model' after data extraction.")

if st.sidebar.button("1. Extract & Clean New Data"):
    with st.spinner("Scraping data..."):
        df = get_data()
        store_data(df)
        st.sidebar.success("Data Updated!")
        st.dataframe(df.head())

if st.sidebar.button("2. Train ML Model"):
    df = load_data()
    if not df.empty:
        with st.spinner("Training Random Forest Model..."):
            train_and_save_model(df)
            st.sidebar.success("Model Trained!")
    else:
        st.sidebar.error("No data found. Extract data first.")

# --- Main Interface ---

st.header("Import Cost Calculator & Estimator")

col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("Enter Car Specifications")
    
    # Input Form
    with st.form("car_form"):
        make = st.selectbox("Make", ["Toyota", "Honda", "Nissan", "Mazda", "Subaru", "Mitsubishi"])
        model = st.text_input("Model (e.g., Axio, Vitz, Fit)", "Axio")
        year = st.slider("Year of Manufacture", 2018, 2024, 2020)
        mileage = st.number_input("Mileage (km)", 0, 200000, 30000)
        engine_cc = st.selectbox("Engine CC", [660, 1000, 1300, 1500, 1800, 2000, 2400, 3000])
        fuel = st.selectbox("Fuel Type", ["Petrol", "Diesel", "Hybrid"])
        trans = st.selectbox("Transmission", ["Automatic", "Manual"])
        body = st.selectbox("Body Type", ["Sedan", "Hatchback", "SUV", "Wagon"])
        
        submitted = st.form_submit_button("Calculate Import Cost")

with col2:
    st.subheader("Market Comparison")
    local_price = st.number_input("Enter Local Kenya Price (KES)", 0, 10000000, 2000000)

if submitted:
    # 1. Predict Price in Japan
    model = load_model()
    if model:
        input_data = {
            'make': make, 'model': model, 'year': year, 
            'mileage_km': mileage, 'engine_cc': engine_cc, 
            'fuel_type': fuel, 'transmission': trans, 'body_type': body
        }
        predicted_jpy = predict_price(model, input_data)
        
        st.info(f"💰 Predicted FOB Price (Japan): **¥{predicted_jpy:,.0f}** (approx. KES {predicted_jpy*0.95:,.0f})")
        
        # 2. Calculate Import Duty
        costs = calculate_import_cost(predicted_jpy, engine_cc)
        
        st.subheader("Cost Breakdown (KES)")
        
        # Display breakdown
        breakdown_df = pd.DataFrame.from_dict(costs, orient='index', columns=['Amount (KES)'])
        st.dataframe(breakdown_df, use_container_width=True)
        
        # 3. Comparison
        total_landed = costs['Total Landed Cost (KES)']
        difference = local_price - total_landed
        
        st.subheader("Savings Analysis")
        if difference > 0:
            st.success(f"✅ You save approx **KES {difference:,.0f}** by importing!")
        else:
            st.error(f"⚠️ Buying locally is cheaper by approx **KES {abs(difference):,.0f}**.")
            
            # Plotly Chart
            import plotly.graph_objects as go
            fig = go.Figure([go.Bar(x=['Local Price', 'Imported Cost'], y=[local_price, total_landed])])
            st.plotly_chart(fig, use_container_width=True)
            
    else:
        st.warning("Model not trained yet. Please train the model in the sidebar.")

# --- Data View ---
st.header("📊 Current Database Listings")
db_data = load_data()
if not db_data.empty:
    st.dataframe(db_data)
else:
    st.info("Database is empty. Click 'Extract Data' in the sidebar.")
