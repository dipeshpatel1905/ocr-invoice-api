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

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Tesseract config
pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'

# FastAPI app
app = FastAPI()

# Enable CORS for your frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.cloudsyncdigital.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Google Sheets setup
SERVICE_ACCOUNT_FILE = 'service_account.json'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SPREADSHEET_ID = '12Dgde7jGtlpJHoefyXiG8tecveQ6qc-mwzUyI3FTPrY'

def get_sheets_service():
    try:
        creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        service = build('sheets', 'v4', credentials=creds)
        logger.info("✅ Google Sheets connected.")
        return service
    except HttpError as error:
        logger.error("❌ Sheets auth error: %s", error)
        raise HTTPException(status_code=500, detail="Google Sheets auth failed.")

def append_to_sheet(sheet_name: str, values: list):
    service = get_sheets_service()
    range_name = f'{sheet_name}!A:Z'
    body = {'values': [values]}
    try:
        service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=range_name,
            valueInputOption='USER_ENTERED',
            insertDataOption='INSERT_ROWS',
            body=body
        ).execute()
        logger.info(f"📄 Row added to {sheet_name}: {values}")
    except HttpError as error:
        logger.error(f"❌ Failed to write to {sheet_name}: {error}")
        raise HTTPException(status_code=500, detail="Sheet append failed.")

def preprocess_image_for_ocr(pil_image):
    logger.info("🔧 Preprocessing image...")
    img_cv = np.array(pil_image)
    if len(img_cv.shape) == 3:
        img_cv = cv2.cvtColor(img_cv, cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(img_cv, (5, 5), 0)
    thresholded = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                        cv2.THRESH_BINARY, 11, 2)
    return Image.fromarray(thresholded)

@app.post("/extract-invoice-data/")
async def extract_invoice_data(image: UploadFile = File(...)):
    logger.info("📥 Uploaded file: %s", image.filename)

    try:
        image_bytes = await image.read()
        pil_image = Image.open(io.BytesIO(image_bytes))
        processed_image = preprocess_image_for_ocr(pil_image)

        raw_text = pytesseract.image_to_string(processed_image, lang='eng', config='--psm 6')
        logger.info("🧾 OCR Result:\n%s", raw_text)

        def safe_search(pattern, text, label):
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            result = match.group(1).strip() if match else 'N/A'
            logger.info(f"🔍 {label}: {result}")
            return result

        # Extracted fields with improved regex
        extracted_data = {
            'Sales_Invoice_No': safe_search(r'invoice\s*(?:no|number)?[:\-]?\s*([A-Z0-9\-]+)', raw_text, 'Sales_Invoice_No'),
            'Customer_Name': safe_search(r'customer\s*[:\-]?\s*(.+?)(?:\n|date|tax|vat)', raw_text, 'Customer_Name'),
            'Date': safe_search(r'date\s*[:\-]?\s*([0-9]{2}[\/\-][0-9]{2}[\/\-][0-9]{2,4})', raw_text, 'Date'),
            'TAX_NUMBER': safe_search(r'(?:tax number|vat number|vat no|tax)\s*[:\-]?\s*([0-9]{5,15})', raw_text, 'TAX_NUMBER'),
            'Company_Name': safe_search(r'(bread\s*basket\s*company)', raw_text, 'Company_Name'),
            'Items': [],
            'Total_Summary': 'N/A',
            'Discount': 'N/A',
            'Net_Amount': 'N/A',
            'Sales_Tax': 'N/A'
        }

        # Append to Sheet1 only if Invoice Number found
        if extracted_data['Sales_Invoice_No'] != 'N/A':
            append_to_sheet('Sheet1', [
                extracted_data['Sales_Invoice_No'],
                extracted_data['Customer_Name'],
                extracted_data['Date'],
                extracted_data['TAX_NUMBER'],
                extracted_data['Company_Name'],
                extracted_data['Total_Summary']
            ])
        else:
            logger.warning("⚠️ Invoice number not found — skipping sheet update.")

        return JSONResponse(content={"status": "success", "data": extracted_data})

    except Exception as e:
        logger.exception("❌ Invoice processing failed")
        raise HTTPException(status_code=500, detail=str(e))
