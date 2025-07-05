import logging, io, re
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from PIL import Image
import pytesseract, numpy as np, cv2
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger()

# Tesseract config
pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'

# FastAPI app setup
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.cloudsyncdigital.com"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# Google Sheets config
SERVICE_ACCOUNT_FILE = 'service_account.json'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SPREADSHEET_ID = '12Dgde7jGtlpJHoefyXiG8tecveQ6qc-mwzUyI3FTPrY'

def get_sheets_service():
    try:
        creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        return build('sheets', 'v4', credentials=creds)
    except Exception as e:
        logger.error("❌ Sheets auth error: %s", e)
        raise HTTPException(500, "Google Sheets auth failed")

def append_to_sheet(sheet, values):
    try:
        svc = get_sheets_service()
        str_values = [str(v) if v is not None else '' for v in values]  # ✅ Cast all to string
        body = {'values': [str_values]}
        svc.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=sheet + "!A:Z",
            valueInputOption='USER_ENTERED',
            insertDataOption='INSERT_ROWS',
            body=body
        ).execute()
        logger.info(f"✅ Appended row to {sheet}: {str_values}")
    except HttpError as e:
        logger.error("❌ Google Sheets API error: %s", e)
        raise HTTPException(500, f"Google Sheets API error: {e}")
    except Exception as e:
        logger.error("❌ Unexpected error writing to sheet: %s", e)
        raise HTTPException(500, "Sheet append failed")

def preprocess_table(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    inv = cv2.bitwise_not(th)

    # Detect vertical & horizontal lines
    vkernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, img.shape[0]//100))
    hkernel = cv2.getStructuringElement(cv2.MORPH_RECT, (img.shape[1]//40, 1))
    vlines = cv2.dilate(cv2.erode(inv, vkernel, iterations=3), vkernel, iterations=3)
    hlines = cv2.dilate(cv2.erode(inv, hkernel, iterations=3), hkernel, iterations=3)

    mask = cv2.addWeighted(vlines, 0.5, hlines, 0.5, 0.0)
    clean = cv2.subtract(inv, mask)
    return clean

def extract_table_cells(clean_img, orig_img):
    conts, _ = cv2.findContours(clean_img, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    cells = []
    for c in conts:
        x,y,w,h = cv2.boundingRect(c)
        if w > 30 and h > 15:
            cell = orig_img[y:y+h, x:x+w]
            text = pytesseract.image_to_string(cell, lang='eng', config='--psm 7').strip()
            cells.append((x, y, text))
    rows = {}
    for x, y, text in cells:
        row_key = y // 20
        rows.setdefault(row_key, []).append((x, text))
    table = []
    for row in sorted(rows):
        table.append([text for x, text in sorted(rows[row])])
    return table

@app.post("/extract-invoice-data/")
async def extract_invoice_data(image: UploadFile = File(...)):
    logger.info("📥 Received %s", image.filename)
    data = {
        'Sales_Invoice_No': 'N/A',
        'Customer_Name': 'N/A',
        'Date': 'N/A',
        'TAX_NUMBER': 'N/A',
        'Company_Name': 'N/A',
        'Items': [],
        'Total_Summary': 'N/A',
        'Discount': 'N/A',
        'Net_Amount': 'N/A',
        'Sales_Tax': 'N/A',
        'table': []
    }

    try:
        buf = await image.read()
        pil = Image.open(io.BytesIO(buf)).convert("RGB")
        img = np.array(pil)
        clean = preprocess_table(img)
        table = extract_table_cells(clean, img)
        data['table'] = table
        logger.info("✅ Extracted table with %d rows", len(table))

        raw = pytesseract.image_to_string(pil, lang='eng', config='--psm 6')
        logger.info("🧾 OCR Text:\n%s", raw)

        def grab(pattern, label):
            m = re.search(pattern, raw, re.IGNORECASE)
            val = m.group(1).strip() if m else 'N/A'
            logger.info(f"{label} → {val}")
            data[label] = val

        grab(r'invoice\s*(?:no|number)?[:\-]?\s*([A-Z0-9\-]+)', 'Sales_Invoice_No')
        grab(r'customer\s*[:\-]?\s*(.+?)(?=\n|date|tax|vat)', 'Customer_Name')
        grab(r'date\s*[:\-]?\s*([0-9]{2}[\/\-][0-9]{2}[\/\-][0-9]{2,4})', 'Date')
        grab(r'(?:tax number|vat)\s*[:\-]?\s*([0-9]{5,15})', 'TAX_NUMBER')
        grab(r'(bread\s*basket\s*company)', 'Company_Name')

        if data['Sales_Invoice_No'] != 'N/A':
            append_to_sheet('Sheet1', [
                data.get('Sales_Invoice_No'),
                data.get('Customer_Name'),
                data.get('Date'),
                data.get('TAX_NUMBER'),
                data.get('Company_Name'),
                data.get('Total_Summary')
            ])
        else:
            logger.warning("⚠️ No invoice number – skipping sheet write")

        return JSONResponse({"status": "success", "data": data})
    except Exception as e:
        logger.exception("❌ Processing failed")
        raise HTTPException(500, str(e))
