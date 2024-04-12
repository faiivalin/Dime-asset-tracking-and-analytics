from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.util.auth import authenticate
from src.module.stockInfo import get_last_available_trading_day_closing_price, check_valid_trading_date
from src.module.importDataToGoogleSheet import import_invest_log_to_google_sheet

from datetime import datetime, timedelta, time

import pandas as pd
import numpy as np


def query_investment_log(spreadsheet_id: str, range_name: str, start_date: datetime.date, end_date: datetime.date):
    try:
        credentials = authenticate()
        service = build("sheets", "v4", credentials=credentials)

        # Call the Sheets API
        sheet = service.spreadsheets()
        result = (
            sheet.values()
            .get(spreadsheetId=spreadsheet_id, range=range_name)
            .execute()
        )
        values = result.get("values", [])
        if not values:
            print("No data found.")
            return None

        # Convert values to a pandas DataFrame
        investment = pd.DataFrame(values[1:], columns=values[0])  # the first row contains headers

        # Convert the first column to datetime
        investment[investment.columns[0]] = pd.to_datetime(investment[investment.columns[0]])

        # Filter DataFrame based on start_date and end_date
        # Ensure start_date and end_date are also datetime objects for comparison
        start_datetime = pd.to_datetime(start_date)
        end_datetime = pd.to_datetime(end_date)

        # Filtering
        mask = (investment[investment.columns[0]] >= start_datetime) & (
                investment[investment.columns[0]] <= end_datetime)
        filtered_investment = investment.loc[mask]

        return filtered_investment

    except HttpError as err:
        print(err)


def process_investment_log(investment_log, spreadsheet_id: str, asset_log_range_name: str, start_date: datetime.date,
                           end_date: datetime.date):
    temp_time = time(8, 30, 00)

    drop_column = ['Type', 'Have Dividend', 'Stock Price (USD)', 'Commission (USD)', 'Tax (USD)', 'Status', 'Note']
    investment_log = investment_log.drop(columns=drop_column, axis=1)
    investment_log["Share"] = investment_log["Share"].astype(np.float64)
    investment_log["Amount (USD)"] = investment_log["Amount (USD)"].astype(np.float64)
    investment_log["Total Amount (USD)"] = investment_log["Total Amount (USD)"].astype(np.float64)

    # Ensure start_date is before or equal to end_date
    if start_date > end_date:
        print("Start date must not be after end date.")
        return None

    date_list = []
    current_date = start_date
    while current_date <= end_date:
        # Append the current_date to the list
        date_list.append(current_date)
        # Move to the next day
        current_date += timedelta(days=1)

    # Process asset value for each day
    for process_date in date_list:
        print("Processing date: ", process_date)
        nyse_temp_datetime = datetime.combine(process_date, temp_time)

        # Filtering investment_log for the current date
        mask = (investment_log['Date'] == pd.to_datetime(process_date))  # Assuming the first column is named 'Date'
        filtered_investment_log = investment_log.loc[mask]

        # Group by 'Product Name' and sum 'Share' while keeping all other columns
        filtered_investment_log = filtered_investment_log.groupby(
            ['Date', 'Port', 'Product Name', 'Sector', 'Industry'], as_index=False).agg(
            {'Share': 'sum', 'Amount (USD)': 'sum', 'Total Amount (USD)': 'sum'})

        asset_log = query_investment_log(spreadsheet_id, asset_log_range_name, process_date - timedelta(days=1),
                                         process_date - timedelta(days=1))
        asset_log = asset_log.drop(
            columns=['Closing Stock Price', 'Valuation', 'Is Market Open', 'Performance', 'Total Performance'], axis=1)
        asset_log["Share"] = asset_log["Share"].astype(np.float64)

        asset_log["Share"] = asset_log["Share"].astype(np.float64)
        asset_log["Amount (USD)"] = asset_log["Amount (USD)"].astype(np.float64)
        asset_log["Total Amount (USD)"] = asset_log["Total Amount (USD)"].astype(np.float64)

        final_df = asset_log

        if not filtered_investment_log.empty:

            final_df = filtered_investment_log

            if not asset_log.empty:
                # Merging on multiple keys; note that 'Date' and other columns not used for merging will
                # be duplicated if not handled
                # Merge DataFrames with an outer join to ensure all records are included
                merged_df = pd.merge(asset_log, filtered_investment_log,
                                     on=['Product Name', 'Port', 'Sector', 'Industry'], how='outer',
                                     suffixes=('_asset', '_filtered'))

                # Fill NaN values in 'Share_filtered' with 0 before summing
                merged_df['Share_filtered'] = merged_df['Share_filtered'].fillna(0)
                merged_df['Share_asset'] = merged_df['Share_asset'].fillna(0)
                merged_df['Amount (USD)_filtered'] = merged_df['Amount (USD)_filtered'].fillna(0)
                merged_df['Amount (USD)_asset'] = merged_df['Amount (USD)_asset'].fillna(0)
                merged_df['Total Amount (USD)_filtered'] = merged_df['Total Amount (USD)_filtered'].fillna(0)
                merged_df['Total Amount (USD)_asset'] = merged_df['Total Amount (USD)_asset'].fillna(0)

                # Sum 'Share' values and update 'Date' if there's a matching record in filtered_investment_log
                merged_df['Share'] = merged_df['Share_asset'] + merged_df['Share_filtered']
                merged_df['Amount (USD)'] = merged_df['Amount (USD)_asset'] + merged_df['Amount (USD)_filtered']
                merged_df['Total Amount (USD)'] = merged_df['Total Amount (USD)_asset'] + merged_df[
                    'Total Amount (USD)_filtered']
                merged_df['Date'] = process_date.strftime('%Y-%m-%d')

                # Select relevant columns and drop duplicates
                final_df = merged_df[
                    ['Date', 'Port', 'Product Name', 'Sector', 'Industry', 'Share', 'Amount (USD)',
                     'Total Amount (USD)']].copy()

                # Ensure no duplicate entries
                final_df = final_df.drop_duplicates(subset=['Port', 'Product Name', 'Sector', 'Industry'])

        elif asset_log.empty:
            print("There isn't have trading data on this date yet.")
            continue
        stock_triggers = final_df['Product Name'].values
        closing_prices = []
        performances = []
        total_performances = []
        valuations = []
        for trigger in stock_triggers:
            print("Fetch closing price of ", trigger)
            closing_price = get_last_available_trading_day_closing_price(stock_name=trigger,
                                                                         target_date=nyse_temp_datetime,
                                                                         user_timezone='America/New_York')
            closing_prices.append(closing_price)

            temp_asset_log = final_df.loc[(final_df["Product Name"] == trigger)]
            temp_asset_log = temp_asset_log.iloc[0]
            share = temp_asset_log['Share']
            amount_usd = temp_asset_log['Amount (USD)']
            total_amount_usd = temp_asset_log['Total Amount (USD)']
            if not share == 0:
                valuation = closing_price * share
                valuations.append(valuation)
                performances.append((valuation - amount_usd) / amount_usd)
                total_performances.append(
                    (valuation - total_amount_usd) / total_amount_usd)
            else:
                valuations.append(0)
                performances.append(0)
                total_performances.append(0)

        is_close = check_valid_trading_date(nyse_temp_datetime, 'America/New_York')

        final_df.insert(1, 'Is Market Open', is_close, True)
        final_df.insert(9, 'Closing Stock Price', closing_prices, True)
        final_df.insert(10, 'Valuation', valuations, True)
        final_df.insert(11, 'Performance', performances, True)
        final_df.insert(12, 'Total Performance', total_performances, True)

        final_df['Date'] = process_date.strftime('%Y-%m-%d')

        print(final_df)

        x = final_df.values.tolist()

        import_invest_log_to_google_sheet(spreadsheet_id, asset_log_range_name, "USER_ENTERED",
                                          x)
