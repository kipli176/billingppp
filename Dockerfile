FROM python:3.11-slim

# Install cron
RUN apt-get update && \
    apt-get install -y cron && \
    rm -rf /var/lib/apt/lists/*

# Set workdir
WORKDIR /app

# Copy dependency list dulu (biar layer cache kepake)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt
ENV TIMEZONE=Asia/Jakarta
RUN apt-get update && apt-get install -y tzdata && \
    ln -fs /usr/share/zoneinfo/Asia/Jakarta /etc/localtime && dpkg-reconfigure -f noninteractive tzdata

# Copy seluruh source code
COPY . .

# Pastikan folder cron_jobs terbaca oleh Python
# (opsional: kalau mau pakai package-import)
# RUN touch cron_jobs/__init__.py

# Setup crontab file
# File ini akan kita buat di langkah berikut
COPY crontab /etc/cron.d/app-cron

# Permission crontab
RUN chmod 0644 /etc/cron.d/app-cron && \
    crontab /etc/cron.d/app-cron

# Direktori log cron
RUN mkdir -p /var/log/cron

# Default command untuk web (akan dioverride di service cron)
CMD ["gunicorn", "-b", "0.0.0.0:5000", "app:create_app()"]
