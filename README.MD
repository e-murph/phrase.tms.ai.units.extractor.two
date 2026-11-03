# Phrase TMS Data Extractor (AI Units & Wordcounts)

A Python utility that bypasses the Phrase TMS web interface to pull "AI Units" and "Wordcount" data directly from the underlying Metabase API. 

## 🚀 Features
* **Excel Export:** Automatically fetches data from two separate Phrase dashboards (AI Units and Wordcounts) and formats the raw JSON response into a clean, easy-to-read Excel spreadsheet.
* **Smart Data Mapping:** Uses a local JSON configuration file to automatically categorise raw project strings into specific "Product" and "Language" reports.
* **Intelligent Sorting:** Automatically sorts project rows by their trailing numerical values and alphabetical language codes.
* **Automated Email Routing:** Compresses reports into targeted `.zip` files and routes specific data subsets (e.g., only Product logs) to different teams (Supports To, CC, and BCC) using a secure Gmail bot.
* **Automation:** Scheduled to run every Sunday via GitHub Actions at 16:00 UTC.
* **Manual Trigger:** A "Run Workflow" button in GitHub for on-demand reports.
* **Secure:** Uses GitHub Repository Secrets to protect sensitive JWT tokens, API endpoints, and email credentials.
* **Self-Updating Security:** Utilizes GitHub Dependabot to automatically monitor and update workflow dependencies every month.

## 📋 Prerequisites
* A GitHub Repository.
* Python 3.10+ (if running locally).
* Required Libraries: `requests`, `pandas`, `openpyxl`, `python-dotenv`.
* A valid Phrase TMS account with access to the AI Units and Wordcount dashboards.
* A free Gmail account (acting as a "Bot") with 2-Step Verification and an **App Password** generated.

## 🛠️ Setup & Configuration

### Step 1: Find the API URLs and Headers
Because the AI Units and Wordcounts live on different dashboards, you must capture **two** separate background requests.
1. Log in to [Phrase Dashboard](https://eu.phrase.com/idm-ui/analytics/genai) and [Analytics](https://cloud.memsource.com/tms/analytics/volume) using Google Chrome and navigate to the pages showing **AI Units by project** and **Word count by project**.
2. Open your browser's Developer Tools (Right-click anywhere -> **Inspect** -> go to the **Network** tab).
3. **Refresh** the page to start recording network traffic.
4. In the Network tab's search/filter bar, type some text from the project name, e.g., `Cloud-Native`.
5. Look for a request that returns a `XXX?parameters=...`. If you click on it and check the **Preview** or **Response** tab, you should see a JSON array containing project names and AI unit numbers.
6. **Right-click** that request -> **Copy** -> **Copy as cURL (bash)**.

### Step 2: Convert to Python Dictionaries
To identify the headers and parameters for both requests:
1. Go to [curlconverter.com](https://curlconverter.com/).
2. Paste the copied cURL commands one at a time.
3. Look at the generated code to find the exact `response url` and the `referer` token (JWT) for **both** the AI Units and the Wordcounts.

### Step 3: Configure Data Mapping (`mapping_settings.json`)
Create a `mapping_settings.json` file in the root directory. This tells the script how to route Phrase project names into specific Product and Language files.
* Add your target languages to the `languages` array.
* Map exact Phrase project names to their respective `products`. 
* *Note: Any project scraped from Phrase that is not in this file will automatically be routed to an "Unmapped_Projects" Excel file, utilising Regex to attempt to rescue the language code.*

### Step 4: Configure GitHub Secrets
To keep credentials safe, do **not** hardcode them. 
1.  Go to the GitHub Repo > **Settings** > **Secrets and variables** > **Actions**.
2.  Click **New repository secret** and add the following:
    * `PHRASE_API_URL` (From Step 2 - AI Units)
    * `PHRASE_REFERER` (From Step 2 - AI Units)
    * `PHRASE_API_URL_WORDS` (From Step 2 - Wordcounts)
    * `PHRASE_REFERER_WORDS` (From Step 2 - Wordcounts)
    * `EMAIL_USERNAME` (Your bot's full @gmail.com address)
    * `EMAIL_PASSWORD` (Your bot's 16-character Google App Password)

### Step 5: Deploy the Workflow
Ensure your `.github/workflows/scrape_stats.yml` is pushed to the repository. This file contains the cron schedule, the environment setup, the `.zip` compression step, and the `dawidd6/action-send-mail` step for email delivery.

## 💻 Usage & Downloading Reports

### Targeted Email Delivery
By default, the script runs every Sunday at 16:00 UTC. It will automatically process the data and use GitHub Actions to route specific files to the right teams:
1. **Workflow Team:** Receives `Weekly_Phrase_Usage_Reports.zip` (containing the Master file, Unmapped projects, and all logs).
2. **SUSE PM Team:** Receives `Weekly_Phrase_SUSE_Cloud-Native_Usage_Reports.zip` (containing *only* the Product logs).
*To modify recipients, simply edit the `to:`, `cc:`, or `bcc:` lines under the respective "Send Email" steps in `.github/workflows/scrape_stats.yml`.*

The ZIP file will contain:
* `SUSE_Cloud-Native_Product_Usage_Stats_[Name].xlsx`
* `SUSE_Cloud-Native_Language_Usage_Stats_[Code].xlsx`
* `Unmapped_Projects_Usage_Stats.xlsx`
* `Master_Phrase_Usage_Stats.xlsx`

### Manual Trigger & Artifact Download
If you need a report right now, or want to download the raw Excel files directly from GitHub without checking your email:
1.  Navigate to the **Actions** tab in the GitHub repository.
2.  Select **Phrase AI Scraper** from the left sidebar.
3.  Click the **Run workflow** dropdown and select the green button.

### How to Download the Excel Files
Since the script runs in the cloud, the file is saved as a GitHub "Artifact":
1.  Once the workflow finishes (green checkmark), click number under **Artifacts** at the top of the page.
2.  Scroll down to the **Artifacts** section at the bottom of the page.
3.  Click **Phrase-AI-Usage-Report** to download a zip file containing the timestamped Excel spreadsheets.

## 🛠️ Local Development
If you want to run the script on your own machine:
1.  Ensure `mapping_settings.json` is in your local directory.
2.  Create a `.env` file in the root folder.
3.  Add the secrets:
    ```env
    PHRASE_API_URL=your_ai_url_here
    PHRASE_REFERER=your_ai_referer_here
    PHRASE_API_URL_WORDS=your_words_url_here
    PHRASE_REFERER_WORDS=your_words_referer_here
    ```
4.  Run `python main.py`.

## ⚠️ Maintenance Note
The `PHRASE_REFERER` and `PHRASE_REFERER_WORDS` variables contain JWT tokens that may expire based on Phrase's security settings. If the GitHub Action starts failing with a `401` or `403` error, or if a specific column returns all zeros, simply repeat the "Copy as cURL" step for that specific dashboard and update the secret in the GitHub Settings.

---

### 📝 License

This project is licensed under the Apache 2.0 License. © 2026 Vistatec, Ltd.

---