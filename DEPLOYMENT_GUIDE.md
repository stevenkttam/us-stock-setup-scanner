# No-code deployment guide

## What you need

- A GitHub account
- A Streamlit Community Cloud account
- No Python installation is required for deployment

## Step 1 — Create the repository

Create a **public** GitHub repository, then upload the contents of this folder to it using GitHub's web interface.

Suggested repository name:

`us-stock-setup-scanner`

Public is the simplest zero-cost route because GitHub Actions standard runners are free for public repositories.

## Step 2 — Deploy the dashboard

Go to Streamlit Community Cloud and connect GitHub.

Create an app using:

- Repository: your new repository
- Branch: `main`
- Main file: `app.py`

The dashboard will initially open in **Sample dashboard** mode. Sample numbers are placeholders and are not live trading signals.

## Step 3 — Build the historical model

In GitHub:

Actions → `Train Scanner Models` → `Run workflow`

This workflow downloads the current US universe, builds the 10-year historical setup set, labels outcomes using the +2R / -1R / 10-session definition, and trains separate Long and Short probability models.

The workflow stores only the compact trained models and metadata in the repository; it does not commit the large raw historical dataset.

## Step 4 — Turn on the daily scan

After successful model training:

Actions → `Live Daily Scan`

The scheduled workflow runs after the US regular session and refreshes `data/latest_scan.parquet`.

## Step 5 — Use the dashboard

Open the Streamlit app and select:

`Live data (when configured)`

The live dashboard will show the current ranked candidates once the scan artifacts and trained models are present.

## Important limitations of the free stack

The whole-US daily scan is the primary use case. A 10-year whole-market historical backtest through a free provider can be slow and may be subject to provider rate limits.

The current-universe list is not a point-in-time historical universe, so historical performance can contain survivorship bias. Treat the first backtest as a research baseline rather than a professionally survivorship-corrected study.

The 5m/15m data layer is intended for shortlisted-name execution confirmation, not for a 10-year whole-market intraday backtest.

The dashboard should remain in research mode until the out-of-sample calibration and live paper-tracking results are satisfactory.
