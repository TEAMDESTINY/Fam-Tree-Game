# Family Tree Telegram Bot - Dockerfile
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set work directory
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc-dev \
    libpq-dev \
    libjpeg-dev \
    zlib1g-dev \
    libpng-dev \
    libcairo2-dev \
    libpango1.0-dev \
    gir1.2-pango-1.0 \
    gir1.2-gtk-3.0 \
    libgirepository-2.0-dev \
    gobject-introspection \
    libglib2.0-dev \
    libglib2.0-dev-bin \
    libffi-dev \
    pkg-config \
    cmake \
    python3-dev \
    # Playwright dependencies
    libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Copy fonts and update font cache
COPY assets/fonts/ /usr/share/fonts/famtree/
RUN fc-cache -f -v

# Copy requirements and install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Install Playwright browsers
RUN playwright install chromium

# Run the bot
CMD ["python", "-m", "bot"]
