FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy

# Install curl and system tools
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Install Ollama
RUN curl -fsSL https://ollama.com/install.sh | sh

# Set working directory
WORKDIR /app

# Copy dependencies and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Expose Hugging Face default port (7860)
EXPOSE 7860

# Startup script: launch Ollama daemon, pull model, and start FastAPI
CMD ollama serve > /dev/null 2>&1 & \
    sleep 3 && \
    ollama pull gemma4:e4b && \
    uvicorn app:app --host 0.0.0.0 --port 7860
