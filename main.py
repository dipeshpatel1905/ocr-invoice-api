# main.py
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from PIL import Image
import io
import pytesseract
import re
import pandas as pd
import numpy as np # For OpenCV image conversion
import cv2 # For image processing

# Google Sheets API imports
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# --- IMPORTANT: Tesseract Path Configuration ---
# On Render using the Dockerfile provided, Tesseract will be installed at /usr/bin/tesseract
pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'

app = FastAPI()

# --- Google Sheets API Configuration ---
# Path to your service account key file (this file must be in your project root)
SERVICE_ACCOUNT_FILE = 'service_account.json'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
# Replace with YOUR actual Spreadsheet ID
# The ID is the long string of characters in the URL between /d/ and /edit
SPREADSHEET_ID = '12Dgde7jGtlpJHoefyXiG8tecveQ6qc-mwzUyI3FTPrY'

def get_sheets_service():
    """Authenticates and returns a Google Sheets API service object."""
    try:
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        service = build('sheets', 'v4', credentials=creds)
        return service
    except Exception as e:
        print(f"Error authenticating with Google Sheets API: {e}")
        # Re-raise as HTTPException so Render logs the full error to client
        raise HTTPException(status_code=500, detail=f"Could not authenticate with Google Sheets: {e}")

def append_to_sheet(sheet_name: str, values: list):
    """Appends a row of values to the specified Google Sheet tab."""
    service = get_sheets_service()
    # Define the range to append to (e.g., 'Sheet1!A:Z')
    range_name = f'{sheet_name}!A:Z'

    body = {
        'values': [values]
    }
    try:
        result = service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=range_name,
            valueInputOption='USER_ENTERED', # Interprets values as if entered by a user (e.g., numbers, dates)
            insertDataOption='INSERT_ROWS', # Ensures new rows are inserted
            body=body
        ).execute()
        updated_cells = result.get('updates', {}).get('updatedCells', 0)
        print(f"Data appended to {sheet_name}. Cells updated: {updated_cells}")
        return True
    except HttpError as error:
        print(f"An HTTP error occurred while appending to sheet {sheet_name}: {error}")
        # Log the full content of the error for more detail
        print(f"Error details: {error.content.decode()}")
        raise HTTPException(status_code=500, detail=f"Failed to append data to Google Sheet {sheet_name}: {error.content.decode()}")
    except Exception as e:
        print(f"An unexpected error occurred while appending to sheet {sheet_name}: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while appending data to Google Sheet {sheet_name}: {e}")

# --- Image Pre-processing Function ---
def preprocess_image_for_ocr(pil_image):
    """
    Applies basic OpenCV pre-processing to an image for better OCR results.
    Converts PIL Image to grayscale. Adaptive thresholding is temporarily
    commented out for debugging.
    """
    # Convert PIL Image to OpenCV format (numpy array)
    img_cv = np.array(pil_image)
    if len(img_cv.shape) == 3: # If image is color, convert to grayscale
        img_cv = cv2.cvtColor(img_cv, cv2.COLOR_RGB2GRAY)

    # --- TEMPORARILY COMMENTED OUT ADAPTIVE THRESHOLDING FOR DEBUGGING ---
    # processed_img_cv = cv2.adaptiveThreshold(
    #     img_cv, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
    # )
    # --- Using the grayscale image directly for now ---
    processed_img_cv = img_cv

    # Convert back to PIL Image for pytesseract
    return Image.fromarray(processed_img_cv)


@app.post("/extract-invoice-data/")
async def extract_invoice_data(image: UploadFile = File(...)):
    """
    Receives an invoice image, performs OCR, extracts structured data,
    and appends it to Google Sheets.
    """
    try:
        # 1. Read image data from upload
        image_bytes = await image.read()
        pil_image = Image.open(io.BytesIO(image_bytes))

        # 2. Pre-process image for better OCR
        processed_pil_image = preprocess_image_for_ocr(pil_image)

        # 3. Perform OCR on the processed image
        # config='--psm 6' is often good for a single block of text (like a form)
        # config='--psm 3' (default) is for automatic page segmentation, try if 6 fails
        raw_text = pytesseract.image_to_string(processed_pil_image, lang='eng', config='--psm 6')
        # You could also try PSM 3 for testing:
        # raw_text = pytesseract.image_to_string(processed_pil_image, lang='eng', config='--psm 3')


        print("\n--- Raw OCR Text (after preprocessing) ---")
        print(raw_text)
        print("--------------------\n")

        # 4. Parse and Extract Data based on your fixed invoice format
        extracted_data = {}

        # Helper function to safely search with regex
        def safe_search(pattern, text, default='N/A', cast_func=str, flags=re.IGNORECASE | re.DOTALL):
            match = re.search(pattern, text, flags)
            if match:
                try:
                    return cast_func(match.group(1).strip())
                except (ValueError, IndexError):
                    pass # Fall through to default if casting or group fails
            return default

        # Header Information - More robust regexes
        extracted_data['Sales_Invoice_No'] = safe_search(r'No:\s*(\d+)', raw_text)

        # Customer: Captures everything after "Customer:" until the next known field (Date, TAX NUMBER, or newline)
        extracted_data['Customer_Name'] = safe_search(r'Customer:\s*(.+?)(?=\n(?:Date|TAX NUMBER|Al-Muqabalein|No|$))', raw_text)
        # Clean up common OCR artifacts in customer name
        extracted_data['Customer_Name'] = extracted_data['Customer_Name'].replace('s) Cle al ¢ 6 8 ae', '').strip()

        extracted_data['Date'] = safe_search(r'Date:\s*(\d{2}/\d{2}/\d{4})', raw_text)

        extracted_data['TAX_NUMBER'] = safe_search(r'TAX NUMBER:\s*(\d+)', raw_text)

        # Company Name: Look for "BREAD BASKET" and then "COMPANY" nearby
        company_name_match = re.search(r'(BREAD BASKET.*COMPANY.*)', raw_text, re.IGNORECASE)
        extracted_data['Company_Name'] = company_name_match.group(1).strip() if company_name_match else 'N/A'
        # Clean up common OCR errors in company name
        extracted_data['Company_Name'] = extracted_data['Company_Name'].replace('% whistle', '').replace('=', '').replace('"', '').strip()

        # Company Address/Contact: capture a block below company name, ending before Sales Invoice or item table start
        company_contact_block_match = re.search(r'BREAD BASKET.*?COMPANY.*?\n(.+?)(?=\n(?:Sales Invoice No|No|Item No|QTY|No\s*Item))', raw_text, re.IGNORECASE | re.DOTALL)
        if company_contact_block_match:
             extracted_data['Company_Address_Contact'] = company_contact_block_match.group(1).replace('\n', ' ').strip()
             # Further refine to remove extraneous characters if needed
             extracted_data['Company_Address_Contact'] = re.sub(r'WO \\ |AlMugabalein, Arman\. Jo a|ad a|infobreadbasteteo con', '', extracted_data['Company_Address_Contact'], flags=re.IGNORECASE).strip()
        else:
             extracted_data['Company_Address_Contact'] = 'N/A'


        # Itemized List (More complex, as it's tabular)
        # This regex is designed to be flexible with spacing and handle potential misreads like 'Saas' or 'Bros"Buns'
        # It attempts to capture: No, Item Name, Qty, Unit, Price, Total Price
        # Look for a line starting with digits, then text, then numbers for Qty, Price, Total Price.
        item_rows = re.findall(
            r'^\s*(\d+)\s+(.+?)\s+([\d.]+)\s+([A-Za-z]+)\s+([\d.]+)\s+([\d.]+)$',
            raw_text,
            re.MULTILINE
        )

        extracted_data['Items'] = []
        for row in item_rows:
            try:
                item_dict = {
                    'Item_No': row[0].strip(),
                    'Item_Name': row[1].replace('"', '').strip(), # Clean up quotes from item name
                    'Qty': int(float(row[2].strip())), # Cast to float first, then int to handle "100.000"
                    'Unit': row[3].strip(),
                    'Price': float(row[4].strip()),
                    'Total_Price': float(row[5].strip())
                }
                extracted_data['Items'].append(item_dict)
            except (ValueError, IndexError) as e:
                print(f"Skipping malformed item row: {row}. Error: {e}")
                continue # Skip rows that don't parse correctly

        # Summary Section - Robust to common OCR errors like 'Tota', 'Sone'
        extracted_data['Total_Summary'] = safe_search(r'(?:Total|Tota|otal)\s*~?\s*([\d.]+)', raw_text, default=0.0, cast_func=float)
        extracted_data['Discount'] = safe_search(r'Discount\s*([\d.]+)', raw_text, default=0.0, cast_func=float)
        extracted_data['Net_Amount'] = safe_search(r'(?:Net|Nets?|Sone)\s*\|\s*([\d.]+)', raw_text, default=0.0, cast_func=float) # Added 'Sone' for common misread
        extracted_data['Sales_Tax'] = safe_search(r'Sales tax\s*\|\s*([\d.]+)', raw_text, default=0.0, cast_func=float) # Changed from colon to pipe for tax

        # Footer
        extracted_data['Note'] = safe_search(r'Note\s*(\d+)', raw_text)


        # --- Append extracted data to Google Sheets ---

        # Data for Sheet1 (Main Invoice Summary)
        # Define the order of columns as they should appear in Sheet1
        sheet1_row_values = [
            extracted_data.get('Sales_Invoice_No', ''),
            extracted_data.get('Customer_Name', ''),
            extracted_data.get('Date', ''),
            extracted_data.get('TAX_NUMBER', ''),
            extracted_data.get('Company_Name', ''),
            extracted_data.get('Company_Address_Contact', ''),
            extracted_data.get('Total_Summary', 0.0),
            extracted_data.get('Discount', 0.0),
            extracted_data.get('Net_Amount', 0.0),
            extracted_data.get('Sales_Tax', 0.0),
            extracted_data.get('Note', '')
        ]
        # Only append to Sheet1 if we successfully got at least the invoice number
        if extracted_data['Sales_Invoice_No'] != 'N/A':
            append_to_sheet('Sheet1', sheet1_row_values)
        else:
            print("Skipping Sheet1 append due to missing Sales Invoice No.")


        # Data for Sheet2 (Itemized List)
        # Each item will be a separate row in Sheet2
        if extracted_data['Items']:
            for item in extracted_data['Items']:
                sheet2_row_values = [
                    extracted_data.get('Sales_Invoice_No', ''), # Link items back to the main invoice
                    item.get('Item_No', ''),
                    item.get('Item_Name', ''),
                    item.get('Qty', 0),
                    item.get('Unit', ''),
                    item.get('Price', 0.0),
                    item.get('Total_Price', 0.0)
                ]
                append_to_sheet('Sheet2', sheet2_row_values)
        else:
            print("No items extracted for Sheet2 append.")


        return JSONResponse(content={"status": "success", "data": extracted_data, "sheet_updated": True})

    except Exception as e:
        # Log the full exception traceback for debugging on Render
        import traceback
        print(f"Error during OCR or Sheet processing: {e}")
        traceback.print_exc()
        # Return a 500 HTTP error with details to the client
        raise HTTPException(status_code=500, detail=f"Failed to process image or update sheet: {e}")
