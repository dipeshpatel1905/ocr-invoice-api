from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from PIL import Image
import io
import pytesseract
import re
import pandas as pd
import numpy as np
import cv2
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'
app = FastAPI()

SERVICE_ACCOUNT_FILE = 'service_account.json'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SPREADSHEET_ID = '12Dgde7jGtlpJHoefyXiG8tecveQ6qc-mwzUyI3FTPrY'

def get_sheets_service():
    """Authenticates and returns a Google Sheets API service object."""
    try:
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        service = build('sheets', 'v4', credentials=creds)
        return service
    except HttpError as error:
        raise HTTPException(status_code=500, detail="Google Sheets authentication error.")

def append_to_sheet(sheet_name: str, values: list):
    """Appends a row of values to the specified Google Sheet tab."""
    service = get_sheets_service()
    range_name = f'{sheet_name}!A:Z'
    body = {'values': [values]}
    try:
        result = service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=range_name,
            valueInputOption='USER_ENTERED',
            insertDataOption='INSERT_ROWS',
            body=body
        ).execute()
        return result.get('updates', {}).get('updatedCells', 0)
    except HttpError as error:
        raise HTTPException(status_code=500, detail=f"Error appending to sheet: {error}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")

def preprocess_image_for_ocr(pil_image):
    img_cv = np.array(pil_image)
    if len(img_cv.shape) == 3:
        img_cv = cv2.cvtColor(img_cv, cv2.COLOR_RGB2GRAY)
    # Use Gaussian Blur to reduce noise before thresholding
    blurred_img = cv2.GaussianBlur(img_cv, (5, 5), 0)
    processed_img_cv = cv2.adaptiveThreshold(
        blurred_img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
    )
    return Image.fromarray(processed_img_cv)

@app.post("/extract-invoice-data/")
async def extract_invoice_data(image: UploadFile = File(...)):
    """Processes invoice image, extracts data, and appends to Google Sheets."""
    try:
        image_bytes = await image.read()
        pil_image = Image.open(io.BytesIO(image_bytes))
        processed_pil_image = preprocess_image_for_ocr(pil_image)
        raw_text = pytesseract.image_to_string(processed_pil_image, lang='eng', config='--psm 6')

        extracted_data = {}

        def safe_search(pattern, text, default='N/A'):
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            return match.group(1).strip() if match else default

        # Improved regex for more accuracy
        extracted_data['Sales_Invoice_No'] = safe_search(r'Invoice No\W*(\d+)', raw_text)
        extracted_data['Customer_Name'] = safe_search(r'Customer:\W*(.+?)(?=\nDate|TAX NUMBER|$)', raw_text)
        extracted_data['Date'] = safe_search(r'Date:\W*(\d{2}/\d{2}/\d{4})', raw_text)
        extracted_data['TAX_NUMBER'] = safe_search(r'TAX NUMBER:\W*(\d+)', raw_text)
        extracted_data['Company_Name'] = safe_search(r'BREAD BASKET\s*COMPANY', raw_text)
        
        items_raw = re.findall(r'^\s*(\d+)\s+(.+?)\s+([\d.]+)\s+([A-Za-z]+)\s+([\d.]+)\s+([\d.]+)', raw_text, re.MULTILINE)
        extracted_data['Items'] = []
        for row in items_raw:
            item_dict = {'Item_No': row[0], 'Item_Name': row[1].replace('"', '').strip(), 'Qty': float(row[2]), 'Unit': row[3], 'Price': float(row[4]), 'Total_Price': float(row[5])}
            extracted_data['Items'].append(item_dict)

        extracted_data['Total_Summary'] = safe_search(r'Total:\W*([\d.]+)', raw_text)
        extracted_data['Discount'] = safe_search(r'Discount:\W*([\d.]+)', raw_text)
        extracted_data['Net_Amount'] = safe_search(r'Net:\W*([\d.]+)', raw_text)
        extracted_data['Sales_Tax'] = safe_search(r'Sales tax:\W*([\d.]+)', raw_text)

        # Append data to Google Sheets
        if extracted_data['Sales_Invoice_No'] != 'N/A':
            sheet1_row_values = [
                extracted_data.get('Sales_Invoice_No', ''),
                extracted_data.get('Customer_Name', ''),
                extracted_data.get('Date', ''),
                extracted_data.get('TAX_NUMBER', ''),
                extracted_data.get('Company_Name', ''),
                extracted_data['Total_Summary']
            ]
            append_to_sheet('Sheet1', sheet1_row_values)

            for item in extracted_data['Items']:
                sheet2_row_values = [
                    extracted_data['Sales_Invoice_No'],
                    item['Item_No'],
                    item['Item_Name'],
                    item['Qty'],
                    item['Unit'],
                    item['Price'],
                    item['Total_Price']
                ]
                append_to_sheet('Sheet2', sheet2_row_values)

        return JSONResponse(content={"status": "success", "data": extracted_data})

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error processing invoice data: {e}")
