import logging
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
import io
import pytesseract
import re
import numpy as np
import cv2
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# --- OCR Setup ---
pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'

# --- FastAPI App Setup ---
app = FastAPI()

# --- CORS for Frontend ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.cloudsyncdigital.com"],  # Change to ["*"] if needed temporarily
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Google Sheets Setup ---
SERVICE_ACCOUNT_FILE = 'service_account.json'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SPREADSHEET_ID = '12Dgde7jGtlpJHoefyXiG8tecveQ6qc-mwzUyI3FTPrY'

def get_sheets_service():
    try:
        creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        service = build('sheets', 'v4', credentials=creds)
        logger.info("✅ Google Sheets service initialized.")
        return service
    except HttpError as error:
        logger.error("❌ Sheets auth error: %s", error)
        raise HTTPException(status_code=500, detail="Google Sheets authentication error.")

def append_to_sheet(sheet_name: str, values: list):
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
        logger.info(f"📄 Appended to {sheet_name}: {values}")
        return result.get('updates', {}).get('updatedCells', 0)
    except HttpError as error:
        logger.error(f"❌ Sheets append error: {error}")
        raise HTTPException(status_code=500, detail=f"Error appending to sheet: {error}")

def preprocess_image_for_ocr(pil_image):
    logger.info("🖼️ Preprocessing image for OCR...")
    img_cv = np.array(pil_image)
    if len(img_cv.shape) == 3:
        img_cv = cv2.cvtColor(img_cv, cv2.COLOR_RGB2GRAY)
    blurred_img = cv2.GaussianBlur(img_cv, (5, 5), 0)
    processed_img_cv = cv2.adaptiveThreshold(
        blurred_img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
    )
    return Image.fromarray(processed_img_cv)

@app.post("/extract-invoice-data/")
async def extract_invoice_data(image: UploadFile = File(...)):
    logger.info("📥 Uploaded file: %s", image.filename)

    try:
        image_bytes = await image.read()
        pil_image = Image.open(io.BytesIO(image_bytes))

        processed_image = preprocess_image_for_ocr(pil_image)
        raw_text = pytesseract.image_to_string(processed_image, lang='eng', config='--psm 6')

        logger.info("🧾 OCR Raw Text:\n%s", raw_text)

        extracted_data = {}

        def safe_search(pattern, text, field, default='N/A'):
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            result = match.group(1).strip() if match else default
            logger.info(f"🔍 {field}: {result}")
            return result

        # Extract fields with improved regex
        extracted_data['Sales_Invoice_No'] = safe_search(r'Invoice\s*No\s*[:\-]?\s*([A-Z0-9\-]+)', raw_text, 'Sales_Invoice_No')
        extracted_data['Customer_Name'] = safe_search(r'Customer\s*[:\-]?\s*(.+?)(?=\n|Date|TAX NUMBER|Tax Number)', raw_text, 'Customer_Name')
        extracted_data['Date'] = safe_search(r'Date\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})', raw_text, 'Date')
        extracted_data['TAX_NUMBER'] = safe_search(r'Tax\s*Number\s*[:\-]?\s*([A-Z0-9]+)', raw_text, 'TAX_NUMBER')
        extracted_data['Company_Name'] = safe_search(r'(Bread\s*Basket\s*Company)', raw_text, 'Company_Name')

        # Extract item rows
        items_raw = re.findall(r'^\s*(\d+)\s+(.+?)\s+([\d.]+)\s+([A-Za-z]+)\s+([\d.]+)\s+([\d.]+)', raw_text, re.MULTILINE)
        extracted_data['Items'] = []
        for row in items_raw:
            item = {
                'Item_No': row[0],
                'Item_Name': row[1].strip(),
                'Qty': float(row[2]),
                'Unit': row[3],
                'Price': float(row[4]),
                'Total_Price': float(row[5])
            }
            extracted_data['Items'].append(item)
            logger.info(f"📦 Item Parsed: {item}")

        # Totals
        extracted_data['Total_Summary'] = safe_search(r'Total\s*[:\-]?\s*([\d.]+)', raw_text, 'Total_Summary')
        extracted_data['Discount'] = safe_search(r'Discount\s*[:\-]?\s*([\d.]+)', raw_text, 'Discount')
        extracted_data['Net_Amount'] = safe_search(r'Net\s*[:\-]?\s*([\d.]+)', raw_text, 'Net_Amount')
        extracted_data['Sales_Tax'] = safe_search(r'Sales\s*Tax\s*[:\-]?\s*([\d.]+)', raw_text, 'Sales_Tax')

        # Append to Google Sheets
        if extracted_data['Sales_Invoice_No'] != 'N/A':
            sheet1 = [
                extracted_data.get('Sales_Invoice_No'),
                extracted_data.get('Customer_Name'),
                extracted_data.get('Date'),
                extracted_data.get('TAX_NUMBER'),
                extracted_data.get('Company_Name'),
                extracted_data['Total_Summary']
            ]
            append_to_sheet('Sheet1', sheet1)

            for item in extracted_data['Items']:
                sheet2 = [
                    extracted_data['Sales_Invoice_No'],
                    item['Item_No'],
                    item['Item_Name'],
                    item['Qty'],
                    item['Unit'],
                    item['Price'],
                    item['Total_Price']
                ]
                append_to_sheet('Sheet2', sheet2)

        logger.info("✅ Invoice processing complete.")
        return JSONResponse(content={"status": "success", "data": extracted_data})

    except Exception as e:
        logger.exception("❌ Exception during invoice processing")
        raise HTTPException(status_code=500, detail=f"Error processing invoice data: {e}")
