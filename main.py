import logging, io, re, os, json, base64, requests
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from PIL import Image
import numpy as np, cv2
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import pytesseract


# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger()

# FastAPI app setup
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.cloudsyncdigital.com"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# Google Sheets config
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SPREADSHEET_ID = '12Dgde7jGtlpJHoefyXiG8tecveQ6qc-mwzUyI3FTPrY'

# Preprocessing for table

def preprocess_table(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    inv = cv2.bitwise_not(th)

    vkernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, img.shape[0]//100))
    hkernel = cv2.getStructuringElement(cv2.MORPH_RECT, (img.shape[1]//40, 1))
    vlines = cv2.dilate(cv2.erode(inv, vkernel, iterations=3), vkernel, iterations=3)
    hlines = cv2.dilate(cv2.erode(inv, hkernel, iterations=3), hkernel, iterations=3)

    mask = cv2.addWeighted(vlines, 0.5, hlines, 0.5, 0.0)
    clean = cv2.subtract(inv, mask)
    return clean

# Extract table cells

def extract_table_cells(clean_img, orig_img):
    conts, _ = cv2.findContours(clean_img, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    cells = []
    for c in conts:
        x, y, w, h = cv2.boundingRect(c)
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

# Google Sheets integration

def get_sheets_service():
    try:
        google_creds_json = os.environ.get("GOOGLE_CREDS")
        if not google_creds_json:
            raise RuntimeError("GOOGLE_CREDS env variable not set")

        creds_dict = json.loads(google_creds_json)
        creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        return build('sheets', 'v4', credentials=creds)
    except Exception as e:
        logger.error("❌ Sheets auth error: %s", e)
        raise HTTPException(500, "Google Sheets auth failed")

def append_to_sheet(sheet, values):
    try:
        svc = get_sheets_service()
        str_values = [str(v) if v is not None else '' for v in values]
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

# Google Vision OCR

def extract_text_google_vision(image_bytes: bytes, api_key: str) -> str:
    try:
        base64_img = base64.b64encode(image_bytes).decode('utf-8')
        payload = {
            "requests": [
                {
                    "image": {"content": base64_img},
                    "features": [{"type": "TEXT_DETECTION"}]
                }
            ]
        }
        url = f"https://vision.googleapis.com/v1/images:annotate?key={api_key}"
        headers = {"Content-Type": "application/json"}
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        result = response.json()
        annotations = result.get('responses', [{}])[0].get('textAnnotations', [])
        return annotations[0].get('description', '') if annotations else ''
    except Exception as e:
        logger.error("❌ Google Vision OCR error: %s", e)
        return ''

# Main endpoint

@app.post("/extract-invoice-data/")
async def extract_invoice_data(image: UploadFile = File(...)):
    logger.info("📥 Received %s", image.filename)
    data = {
        'Sales_Invoice_No': 'N/A',
        'Customer_Name': 'N/A',
        'Date': 'N/A',
        'TAX_NUMBER': 'N/A',
        'Company_Name': 'الشركة الأردنية الوطنية للتجارة الدولية',
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

        vision_key = os.environ.get("AIzaSyCSESC-OnHIWZ8jH4exxeeRiy9v_-sV2YE")
        if not vision_key:
            raise RuntimeError("VISION_API_KEY not set in env")

        raw = extract_text_google_vision(buf, vision_key)
        logger.info("🧾 OCR Text:\n%s", raw)

        def grab(pattern, label):
            m = re.search(pattern, raw)
            val = m.group(1).strip() if m else 'N/A'
            logger.info(f"{label} → {val}")
            data[label] = val

        grab(r'فاتورة\s+رقم\s*[:\-]?\s*(\d+)', 'Sales_Invoice_No')
        grab(r'اسم\s+المندوب\s*[:\-]?\s*(.+)', 'Customer_Name')
        grab(r'تاريخ\s+الفاتورة\s*[:\-]?\s*([0-9]{4}/[0-9]{2}/[0-9]{2})', 'Date')
        grab(r'رقم\s+ضريبة\s+المبيعات\s*[:\-]?\s*([0-9]+)', 'TAX_NUMBER')
        grab(r'إجمالي\s+الفاتورة\s*[:\-]?\s*([\d.,]+)', 'Total_Summary')
        grab(r'ضريبة\s+المبيعات\s*[:\-]?\s*([\d.,]+)', 'Sales_Tax')
        grab(r'مجموع\s+الخصم\s*[:\-]?\s*([\d.,]+)', 'Discount')
        grab(r'القيمة\s+المطلوبة\s*[:\-]?\s*([\d.,]+)', 'Net_Amount')

        for row in table:
            if any("burger" in cell.lower() or "slice" in cell.lower() for cell in row):
                item = {
                    "Item_Name": next((c for c in row if "burger" in c.lower() or "slice" in c.lower()), "N/A"),
                    "Quantity": next((c for c in row if re.match(r"^\d+$", c)), "N/A"),
                    "Unit_Price": next((c for c in row if re.match(r"^\d+\.?\d*$", c)), "N/A"),
                    "Total": next((c for c in row[::-1] if re.match(r"^\d+\.?\d*$", c)), "N/A")
                }
                data['Items'].append(item)

        if data['Sales_Invoice_No'] != 'N/A':
            append_to_sheet('Invoices', [
                data['Sales_Invoice_No'], data['Customer_Name'], data['Date'],
                data['TAX_NUMBER'], data['Company_Name'], data['Total_Summary'],
                data['Sales_Tax'], data['Discount'], data['Net_Amount']
            ])
            for item in data['Items']:
                append_to_sheet('Invoice_Lines', [
                    data['Sales_Invoice_No'],
                    item.get("Item_Name", ""),
                    item.get("Quantity", ""),
                    item.get("Unit_Price", ""),
                    item.get("Total", "")
                ])
        else:
            logger.warning("⚠️ No invoice number – skipping sheet write")

        return JSONResponse({"status": "success", "data": data})
    except Exception as e:
        logger.exception("❌ Processing failed")
        raise HTTPException(500, str(e))
