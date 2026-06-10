<img width="749" height="499" alt="Screenshot From 2025-05-13 21-19-48" src="https://github.com/user-attachments/assets/3b2d2768-316e-4a29-a576-67c44951fad6" />
# Japan Car Import Advisory Platform

A Data Science project that helps Kenyan buyers compare the cost of importing cars from Japan versus buying locally. It uses machine learning to predict Japanese car prices and a custom calculator to estimate KRA taxes and total landed cost.

## Features

1.  **Data Pipeline**: Scrapes/Generates data for cars (2018+) and stores it in SQLite.
2.  **Machine Learning**: Random Forest Regressor to predict car prices in JPY based on Make, Model, Year, Mileage, etc.
3.  **Cost Calculator**: Computes Import Duty, Excise Duty, VAT, IDF, and RDL based on current Kenyan laws.
4.  **Comparison**: Compares Total Landed Cost against user-inputted local prices.

## Tech Stack

-   **Language**: Python 3.8+
-   **Database**: SQLite
-   **ML Library**: Scikit-Learn
-   **Web Framework**: Streamlit

## Installation & Setup

1.  **Clone/Download the project files.**

2.  **Install Dependencies:**
    Open your terminal in the project folder and run:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Run the Application:**
    ```bash
    streamlit run app.py
    ```

## How to Use

1.  Once the app opens in your browser, look at the **Sidebar**.
2.  Click **"1. Extract & Clean New Data"**. (This generates mock data simulating SBT/BeForward listings).
3.  Click **"2. Train ML Model"**. This trains the price prediction model on the generated data.
4.  In the main area, enter car details (e.g., Toyota Axio, 2020, 1500cc).
5.  Enter a "Local Kenya Price" you found on a site like Cheki or Jiji.
6.  Click **"Calculate Import Cost"**.
7.  View the predicted price, the tax breakdown, and whether you save money by importing.
