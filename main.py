# main.py
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from PIL import Image
import io
import pytesseract
import re
import pandas as pd # To easily handle tabular data before returning

# --- IMPORTANT: Tesseract Path Configuration ---
# On Render using the Dockerfile provided, Tesseract will be installed at /usr/bin/tesseract
# If you were running this locally without Docker, you'd need to set the path like:
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe' # For Windows
# pytesseract.pytesseract.tesseract_cmd = '/usr/local/bin/tesseract' # For macOS (might vary)
# But for Render with the Dockerfile, this line will work:
pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'

app = FastAPI()

@app.post("/extract-invoice-data/")
async def extract_invoice_data(image: UploadFile = File(...)):
    """
    Receives an invoice image, performs OCR, and extracts structured data.
    """
    try:
        # 1. Read image data
        image_bytes = await image.read()
        pil_image = Image.open(io.BytesIO(image_bytes))

        # 2. Perform OCR
        # We can try to specify a language (eng for English) and PSM (Page Segmentation Mode)
        # PSM 6 is good for a single uniform block of text.
        # PSM 3 (default) for automatic page segmentation, might also work.
        raw_text = pytesseract.image_to_string(pil_image, lang='eng', config='--psm 6')

        # --- For Debugging: Print raw text to logs (useful during development) ---
        print("\n--- Raw OCR Text ---")
        print(raw_text)
        print("--------------------\n")

        # 3. Parse and Extract Data based on your fixed invoice format
        extracted_data = {}

        # Header Information
        extracted_data['Sales_Invoice_No'] = re.search(r'No:\s*(\d+)', raw_text, re.IGNORECASE)
        extracted_data['Sales_Invoice_No'] = extracted_data['Sales_Invoice_No'].group(1).strip() if extracted_data['Sales_Invoice_No'] else 'N/A'

        # This regex will try to capture the customer name until a newline or another known field
        customer_match = re.search(r'Customer:\s*(.+?)(?=\n(Date|TAX NUMBER|Al-Muqabalein|$))', raw_text, re.IGNORECASE | re.DOTALL)
        if customer_match:
            # Clean up potential extra lines or leading/trailing whitespace
            extracted_data['Customer_Name'] = customer_match.group(1).replace('\n', ' ').strip()
        else:
            extracted_data['Customer_Name'] = 'N/A'

        extracted_data['Date'] = re.search(r'Date:\s*(\d{2}/\d{2}/\d{4})', raw_text)
        extracted_data['Date'] = extracted_data['Date'].group(1).strip() if extracted_data['Date'] else 'N/A'

        extracted_data['TAX_NUMBER'] = re.search(r'TAX NUMBER:\s*(\d+)', raw_text, re.IGNORECASE)
        extracted_data['TAX_NUMBER'] = extracted_data['TAX_NUMBER'].group(1).strip() if extracted_data['TAX_NUMBER'] else 'N/A'

        extracted_data['Company_Name'] = re.search(r'(BREAD BASKET -COMPANY-)', raw_text, re.IGNORECASE)
        extracted_data['Company_Name'] = extracted_data['Company_Name'].group(1).strip() if extracted_data['Company_Name'] else 'N/A'

        # For address/contact, it's a bit harder to precisely isolate with a simple regex
        # You might get the general block and then refine, or treat it as one string.
        # For simplicity, we'll try to get the line below company name
        company_address_match = re.search(r'BREAD BASKET -COMPANY-\n(.+?)(?=\nSales Invoice No:)', raw_text, re.IGNORECASE | re.DOTALL)
        if company_address_match:
             extracted_data['Company_Address_Contact'] = company_address_match.group(1).replace('\n', ' ').strip()
        else:
             extracted_data['Company_Address_Contact'] = 'N/A'


        # Itemized List (More complex, as it's tabular)
        # We'll look for lines that fit the pattern of an item row.
        # This regex is an attempt to capture the columns: Item No, Item Name, Qty, Unit, Price, Total Price
        # It's flexible with spaces between columns.
        item_rows = re.findall(
            r'^\s*(\d+)\s+(.+?)\s+(\d+)\s+([A-Za-z]+)\s+([\d.]+)\s+([\d.]+)$',
            raw_text,
            re.MULTILINE
        )
        # Convert to a list of dictionaries for easier Excel export later
        extracted_data['Items'] = []
        for row in item_rows:
            item_dict = {
                'Item_No': row[0].strip(),
                'Item_Name': row[1].strip(),
                'Qty': int(row[2].strip()),
                'Unit': row[3].strip(),
                'Price': float(row[4].strip()),
                'Total_Price': float(row[5].strip())
            }
            extracted_data['Items'].append(item_dict)

        # Summary Section
        extracted_data['Total_Summary'] = re.search(r'Total:\s*([\d.]+)', raw_text, re.IGNORECASE)
        extracted_data['Total_Summary'] = float(extracted_data['Total_Summary'].group(1).strip()) if extracted_data['Total_Summary'] else 0.0

        extracted_data['Discount'] = re.search(r'Discount:\s*([\d.]+)', raw_text, re.IGNORECASE)
        extracted_data['Discount'] = float(extracted_data['Discount'].group(1).strip()) if extracted_data['Discount'] else 0.0

        extracted_data['Net_Amount'] = re.search(r'Net:\s*([\d.]+)', raw_text, re.IGNORECASE)
        extracted_data['Net_Amount'] = float(extracted_data['Net_Amount'].group(1).strip()) if extracted_data['Net_Amount'] else 0.0

        extracted_data['Sales_Tax'] = re.search(r'Sales tax:\s*([\d.]+)', raw_text, re.IGNORECASE)
        extracted_data['Sales_Tax'] = float(extracted_data['Sales_Tax'].group(1).strip()) if extracted_data['Sales_Tax'] else 0.0

        # Footer
        extracted_data['Note'] = re.search(r'Note:\s*(\d+)', raw_text, re.IGNORECASE)
        extracted_data['Note'] = extracted_data['Note'].group(1).strip() if extracted_data['Note'] else 'N/A'


        # Prepare data for potential Excel structure (flat fields + list of items)
        # For Excel, you'd typically want one row per invoice, with item details
        # either flattened (if only a few items) or handled separately.
        # For this API, we return structured JSON. The client can then use this JSON to build Excel.

        return JSONResponse(content={"status": "success", "data": extracted_data})

    except Exception as e:
        # Log the full exception for debugging on Render
        import traceback
        print(f"Error during OCR processing: {e}")
        traceback.print_exc() # This will print the full stack trace to Render logs
        raise HTTPException(status_code=500, detail=f"Failed to process image: {e}")
