#!/bin/bash

# Configuration
PROJECT_DIR="/Users/damikim/restaurant-crawler"
LOG_DIR="$PROJECT_DIR/logs"
DATE=$(date +%Y-%m-%d)
LOG_FILE="$LOG_DIR/crawl_$DATE.log"

# Create logs directory if it doesn't exist
mkdir -p "$LOG_DIR"

cd "$PROJECT_DIR" || exit

echo "--- Starting Full Restaurant Crawl Automation ($DATE) ---" | tee -a "$LOG_FILE"

# 1. Google Restaurant Search
echo "[$(date)] Step 1: Running Google Restaurant Search..." | tee -a "$LOG_FILE"
/usr/bin/python3 google_restaurant_search.py >> "$LOG_FILE" 2>&1

if [ $? -eq 0 ]; then
    echo "[$(date)] Step 1 Success: google_restaurants.csv generated." | tee -a "$LOG_FILE"
else
    echo "[$(date)] Step 1 Failure: Check logs." | tee -a "$LOG_FILE"
    exit 1
fi

# 2. Naver Detailed Crawl
echo "[$(date)] Step 2: Running Naver Detailed Crawl..." | tee -a "$LOG_FILE"
/usr/bin/python3 naver_place_antigravity_patched.py >> "$LOG_FILE" 2>&1

if [ $? -eq 0 ]; then
    echo "[$(date)] Step 2 Success: Data upserted to DB." | tee -a "$LOG_FILE"
else
    echo "[$(date)] Step 2 Failure: Check logs." | tee -a "$LOG_FILE"
    exit 1
fi

echo "--- Full Crawl Automation Finished Successfully ($DATE) ---" | tee -a "$LOG_FILE"
