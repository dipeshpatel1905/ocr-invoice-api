# Use a slim Python image for smaller size
FROM python:3.9-slim-buster

# Install Tesseract OCR and its language data (English)
# Add OpenCV dependencies (libgl1, libsm6, libxrender1)
# These are common dependencies required by opencv-python on Linux
RUN apt-get update && \
    apt-get install -y tesseract-ocr libtesseract-dev tesseract-ocr-eng \
                       libgl1 libsm6 libxrender1 && \
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
