# Use the official Playwright image (Browsers and deps are ALREADY baked into this)
FROM mcr.microsoft.com/playwright/python:v1.58.0-jammy

# Set working directory
WORKDIR /app

# Install FFmpeg (Required for video stitching)
# Note: Added -y to assume yes, and cleaned up apt cache to keep image small
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Copy requirements and install python dependencies
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
