"""Replace the Mongo `products` collection with the catalog in assets/products.json (the 4 NTS tees).
Touches only products; users, orders etc. are left alone (unlike migrate_to_mongo.py)."""
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils.db import write_json, mongo_db

if __name__ == '__main__':
    if mongo_db is None:
        sys.exit("MongoDB not reachable; set MONGO_URI in .env")
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', 'products.json')) as f:
        products = json.load(f)
    write_json('assets/products.json', products)
    print(f"Seeded {len(products)} products")
