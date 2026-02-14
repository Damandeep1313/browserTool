# Use the official Playwright image (includes Python & Browsers)
FROM mcr.microsoft.com/playwright/python:v1.41.0-jammy

# Set working directory
WORKDIR /app

# Install FFmpeg (Required for video stitching)
RUN apt-get update && apt-get install -y ffmpeg

# Copy requirements and install dependencies
# (Create a requirements.txt file with: fastapi, uvicorn, playwright, openai, cloudinary, python-dotenv)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy your code
COPY main.py .

# Create the scans folder so permissions are correct
RUN mkdir scans

# Expose the port
EXPOSE 8000

# Run the server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]