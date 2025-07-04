# Use a slim Python image for smaller size
FROM python:3.9-slim-buster

# Install Tesseract OCR and its language data (English)
# apt-get update: Updates the list of available packages
# apt-get install -y: Installs packages without asking for confirmation
# tesseract-ocr: The Tesseract OCR engine itself
# libtesseract-dev: Development libraries for Tesseract (sometimes needed by pytesseract bindings)
# tesseract-ocr-eng: English language data for Tesseract
# apt-get clean & rm -rf: Cleans up downloaded package files to keep the image small
RUN apt-get update && \
    apt-get install -y tesseract-ocr libtesseract-dev tesseract-ocr-eng && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code into the container
COPY . .

# Expose the port your FastAPI application will listen on
EXPOSE 8000

# Define the command to run your FastAPI application using Uvicorn
# 0.0.0.0 makes the app accessible from outside the container
# $PORT is an environment variable provided by Render that tells your app which port to listen on
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
