import requests
import csv
import logging
import os
import time
from typing import List, Dict

# =========================
# Search Configuration
# =========================
# Replace with your Google Maps API Key
GOOGLE_API_KEY = "AIzaSyCEYNHmxwi-XkH2rSXuadzy3Gib2lylLqU"

# =========================
# Query Generator Configuration
# =========================
AREAS = ["삼평동", "백현동", "수내동"]

# Key roads per area
ROADS = {
    "삼평동": ["판교로", "대왕판교로", "동판교로", "판교역로", "봇들마을", "판교테크노밸리"],
    "백현동": ["판교역로", "동판교로", "백현로", "판교카페거리", "백현마을"],
    "수내동": ["정자로", "백현로", "수내로", "황새울로", "양지마을", "파크타운", "수내역"]
}

# High-density buildings/complexes
BUILDINGS = [
    "판교 유스페이스", "현대백화점 판교점",
    "판교 아브뉴프랑"
]

# User requested categories
CATEGORIES = [
    "한식", "중식", "일식", "양식", "아시안", "고기", "해산물", 
    "치킨", "이자카야", "요리주점", "와인바", "호프", "분식, 바(BAR)"
]

def generate_sub_queries() -> List[str]:
    queries = set()
    
    # 1. Road + Category partitioning
    for area, road_list in ROADS.items():
        for road in road_list:
            for cat in CATEGORIES:
                # e.g. "삼평동 판교로 한식"
                queries.add(f"{area} {road} {cat}")
                
    # 2. Building + Category partitioning
    for bld in BUILDINGS:
        for cat in CATEGORIES:
            # e.g. "현대백화점 판교점 일식"
            queries.add(f"{bld} {cat}")
            
    # 3. Area + Category partitioning (General sweep)
    for area in AREAS:
        for cat in CATEGORIES:
            queries.add(f"{area} {cat}")
            
    return sorted(list(queries))

SUB_QUERIES = generate_sub_queries()

OUTPUT_FILE = "google_restaurants.csv"

# =========================
# Logging
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("GoogleSearch")

# =========================
# Google Places Search (New API v1)
# =========================
def search_restaurants(query: str) -> List[Dict[str, str]]:
    """
    Search for restaurants using Google Places API (New) with a specific query.
    Fetches results using the New Places API (v1) endpoint.
    """
    url = "https://places.googleapis.com/v1/places:searchText"
    
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_API_KEY,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,nextPageToken"
    }
    
    payload = {
        "textQuery": query,
        "languageCode": "ko"
    }
    
    results = []
    
    while True:
        try:
            if "pageToken" not in payload:
                log.info(f"Searching (New API) for: {query}")
            
            response = requests.post(url, json=payload, headers=headers)
            data = response.json()
            
            if response.status_code != 200:
                log.error(f"Google API Error ({response.status_code}): {data.get('error', {}).get('message', 'Unknown error')}")
                break
            
            places = data.get("places", [])
            for place in places:
                name = place.get("displayName", {}).get("text", "Unknown Name")
                address = place.get("formattedAddress", "Unknown Address")
                results.append({
                    "name": name,
                    "address": address,
                    "query_source": query
                })
            
            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break
                
            log.info("Fetching next page of results...")
            payload["pageToken"] = next_page_token
            time.sleep(1)
                
        except Exception as e:
            log.error(f"Failed to search for {query}: {e}")
            break
            
    return results

# =========================
# Main Execution
# =========================
def main():
    if GOOGLE_API_KEY == "AIzaSyCEYNHmxwi-XkH2rSXuadzy3Gib2lylLqU" == False: # Just a check for default
        log.error("Please set your GOOGLE_API_KEY in the script.")
        return

    all_restaurants = []
    
    for query in SUB_QUERIES:
        log.info(f"--- Processing Sub-query: {query} ---")
        restaurants = search_restaurants(query)
        all_restaurants.extend(restaurants)
        time.sleep(1) 
        
    if not all_restaurants:
        log.warning("No restaurant data collected.")
        return

    # De-duplicate by name and address
    seen = set()
    unique_restaurants = []
    for res in all_restaurants:
        key = (res["name"], res["address"])
        if key not in seen:
            seen.add(key)
            unique_restaurants.append(res)

    # Save to CSV
    log.info(f"Saving {len(unique_restaurants)} results to {OUTPUT_FILE}")
    try:
        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=["name", "address", "query_source"])
            writer.writeheader()
            writer.writerows(unique_restaurants)
        log.info("Successfully saved results.")
    except Exception as e:
        log.error(f"Failed to save CSV: {e}")

if __name__ == "__main__":
    main()
