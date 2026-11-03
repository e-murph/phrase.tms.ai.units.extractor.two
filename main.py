import os
import requests
import pandas as pd
import json
import re
from datetime import datetime
from openpyxl.styles import Font

# Load local .env for local testing
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# 1. Retrieve secrets securely from .env
url_ai = os.getenv("PHRASE_API_URL")
referer_ai = os.getenv("PHRASE_REFERER")

url_words = os.getenv("PHRASE_API_URL_WORDS")
referer_words = os.getenv("PHRASE_REFERER_WORDS")

# 2. Build the Metabase-specific headers for BOTH API requests
# Headers for the AI Units fetch
headers_ai = {
    'accept': 'application/json',
    'accept-language': 'en-US,en;q=0.9',
    'content-type': 'application/json',
    'referer': referer_ai,
    'x-metabase-client': 'embedding-iframe',
    'x-metabase-embedded': 'true',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

# Headers for the Wordcount fetch
headers_words = {
    'accept': 'application/json',
    'accept-language': 'en-US,en;q=0.9',
    'content-type': 'application/json',
    'referer': referer_words,
    'x-metabase-client': 'embedding-iframe',
    'x-metabase-embedded': 'true',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

my_params = {
    "date": "thisyear"
}

# Helper function to add a Total row, auto-fit Excel columns, and save
def save_and_format_excel(dataframe, filename):
    # Make a copy AND reset the row numbers so the Total row always drops to the absolute bottom
    df_to_save = dataframe.copy().reset_index(drop=True)

    # Create an empty row dictionary matching the columns
    total_row = {col: "" for col in df_to_save.columns}
    if 'Project Name' in total_row:
        total_row['Project Name'] = "TOTAL"

    # Calculate totals dynamically for both columns
    if 'AI Units' in df_to_save.columns:
        total_row['AI Units'] = df_to_save['AI Units'].sum()
    # Look for "Wordcount"
    if 'Wordcount' in df_to_save.columns:
        total_row['Wordcount'] = df_to_save['Wordcount'].sum()

    # Append the Total row to the bottom of the dataframe
    df_to_save.loc[len(df_to_save)] = total_row

    # Export and format
    with pd.ExcelWriter(filename, engine='openpyxl') as writer:
        df_to_save.to_excel(writer, index=False, sheet_name='Data')
        worksheet = writer.sheets['Data']

        # 1. Auto-fit columns with a MAX WIDTH constraint
        for idx, col in enumerate(df_to_save.columns):
            # Calculate how wide the data actually is
            max_length = max(df_to_save[col].astype(str).map(len).max(), len(col))
            ideal_width = max_length + 2
            
            # Apply the 100 character cap to specific columns
            if col in ['Project Name', 'Product Name']:
                final_width = min(ideal_width, 100)
            else:
                final_width = ideal_width
                
            worksheet.column_dimensions[chr(65 + idx)].width = final_width

        # 2. Make the Total row bold
        for cell in worksheet[worksheet.max_row]:
            cell.font = Font(bold=True)

    print(f"📂 Created: {filename}")


def extract_ai_units():
    print("🚀 Fetching data from Phrase TMS...")

    if not url_ai or not referer_ai:
        print("❌ Error: PHRASE_API_URL or PHRASE_REFERER missing. Check your .env file.")
        return

    try:
        # ==========================================
        # FETCH 1: AI UNITS
        # ==========================================
        response_ai = requests.get(url_ai, params=my_params, headers=headers_ai, timeout=30)
        
        if response_ai.status_code not in [200, 202]:
            print(f"❌ Failed AI fetch. Status code: {response_ai.status_code}")
            return

        rows_ai = response_ai.json().get('data', {}).get('rows', [])
        
        extracted_ai = []
        for row in rows_ai:
            extracted_ai.append({
                "Project Name": row[0],
                "AI Units": float(row[3])
            })
            
        df_ai = pd.DataFrame(extracted_ai)

        # ==========================================
        # FETCH 2: WORDCOUNTS
        # ==========================================
        df_words = pd.DataFrame() 
        if url_words:
            print("🚀 Fetching Wordcounts...")
            response_words = requests.get(url_words, params=my_params, headers=headers_words, timeout=30)
            
            if response_words.status_code in [200, 202]:
                rows_words = response_words.json().get('data', {}).get('rows', [])
                
                extracted_words = []
                for row in rows_words:
                    try:
                        # Grab the 5th item in the array (Index 4)
                        raw_words = str(row[4]).replace(',', '') 
                        
                        # Safely convert to a number
                        try:
                            word_val = float(raw_words)
                        except ValueError:
                            word_val = 0.0
                            
                        extracted_words.append({
                            "Project Name": row[0],
                            "Wordcount": word_val
                        })
                    except IndexError:
                        pass # Skip broken rows silently
                        
                # Create the dataframe ONLY if we successfully extracted data
                if extracted_words:
                    df_words = pd.DataFrame(extracted_words)
                else:
                    df_words = pd.DataFrame(columns=['Project Name', 'Wordcount'])
            else:
                print(f"⚠️ Failed Wordcount fetch. Status: {response_words.status_code}")
        else:
            print("⚠️ Skipping Wordcounts: PHRASE_API_URL_WORDS not found in environment.")

        # ==========================================
        # MERGE THE DATA
        # ==========================================
        if not df_words.empty:
            # Outer join stitches them together. If a project is missing from one list, it survives.
            df = pd.merge(df_ai, df_words, on='Project Name', how='outer')
            # Replace missing NaN values with 0
            df['AI Units'] = df['AI Units'].fillna(0)
            df['Wordcount'] = df['Wordcount'].fillna(0)
        else:
            # Fallback if the Wordcount fetch fails
            df = df_ai
            df['Wordcount'] = 0.0

        print(f"✅ Success! Merged data for {len(df)} total projects. Processing mapping...")

        # 3. Read Settings and Map the Data
        try:
            with open('mapping_settings.json', 'r', encoding='utf-8') as f:
                settings = json.load(f)
        except FileNotFoundError:
            print("❌ Error: mapping_settings.json not found. Exporting raw data only.")
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
            save_and_format_excel(df, f"Raw_Phrase_Data_{timestamp}.xlsx")
            return

        # Create reverse-lookup dictionary mapping "Phrase Project Name" -> Product/Language
        project_map = {}
        for prod in settings['products']:
            for lang_code, phrase_proj_name in prod['projects'].items():
                project_map[phrase_proj_name] = {
                    "Product Name": prod['product_name'],
                    "Language": lang_code
                }

        # Apply mapping with Regex fallback for Unmapped projects
        products, languages = [], []
        lang_pattern = re.compile(r'\b([a-z]{2}-[A-Z]{2})\b')

        for proj in df['Project Name']:
            if proj in project_map:
                products.append(project_map[proj]['Product Name'])
                languages.append(project_map[proj]['Language'])
            else:
                products.append("Unmapped")

                # Get the language code from the project name
                match = lang_pattern.search(proj)
                if match:
                    languages.append(match.group(1))
                else:
                    languages.append("Unknown")

        df['Product Name'] = products
        df['Language'] = languages

        # --- SORTING LOGIC ---
        df['_sort_num'] = df['Project Name'].str.extract(r'(\d+)$').astype(float).fillna(0)
        df = df.sort_values(by=['_sort_num', 'Language'], ascending=[True, True])
        df = df.drop(columns=['_sort_num'])
        # -------------------------

        # --- COLUMN REORDERING ---
        df = df[['Project Name', 'Product Name', 'Language', 'AI Units', 'Wordcount']]

        # 4. Generate the Output Files
        timestamp = datetime.now().strftime("%Y-%m-%d")
        print("\n📊 Generating Reports...")

        # A. Create Product Logs
        for product_name, group_df in df[df['Product Name'] != 'Unmapped'].groupby('Product Name'):
            safe_name = product_name.replace(" ", "_").replace("/", "-")
            save_and_format_excel(group_df, f"SUSE_Cloud-Native_Product_Usage_Stats_{safe_name}_{timestamp}.xlsx")

        # B. Create Language Logs
        for lang_code, group_df in df[(df['Language'] != 'Unmapped') & (df['Language'] != 'Unknown')].groupby('Language'):
            save_and_format_excel(group_df, f"SUSE_Cloud-Native_Language_Usage_Stats_{lang_code}_{timestamp}.xlsx")

        # C. Create Unmapped/Orphan Log
        unmapped_df = df[df['Product Name'] == 'Unmapped'].copy()
        if not unmapped_df.empty:
            unmapped_df = unmapped_df[['Project Name', 'Language', 'AI Units', 'Wordcount']]
            save_and_format_excel(unmapped_df, f"Unmapped_Projects_Usage_Stats_{timestamp}.xlsx")

        # D. Save the Master File
        save_and_format_excel(df, f"Master_Phrase_Usage_Stats_{timestamp}.xlsx")

        print("\n✅ All logs generated successfully!")

    except Exception as e:
        print(f"❌ An error occurred: {e}")

if __name__ == "__main__":
    extract_ai_units()