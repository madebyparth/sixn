import os
import sys
import json
from dotenv import load_dotenv

# Add project root to python path to import utils
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

load_dotenv()

from utils.db import write_json, mongo_db

def migrate():
    if mongo_db is None:
        print("MongoDB connection is not active. Please start MongoDB first or set MONGO_URI in .env.")
        sys.exit(1)
        
    files_to_migrate = [
        ('data/users.json', []),
        ('data/orders.json', []),
        ('data/ratings.json', []),
        ('data/reports.json', []),
        ('data/wallet_transactions.json', []),
        ('data/notifications.json', []),
        ('data/coupons.json', []),
        ('data/otps.json', {}),
        ('data/shiprocket_token.json', {}),
        ('assets/products.json', []),
        ('assets/content.json', {})
    ]
    
    print("Starting database migration to MongoDB...")
    for filepath, default_val in files_to_migrate:
        abs_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filepath)
        if os.path.exists(abs_path):
            try:
                with open(abs_path, 'r') as f:
                    data = json.load(f)
                
                # Print stats
                if isinstance(data, list):
                    item_count = len(data)
                elif isinstance(data, dict):
                    item_count = len(data.keys())
                else:
                    item_count = 1
                    
                print(f"Migrating {filepath} ({item_count} items/keys)...")
                # Write to mongo using our helper
                write_json(filepath, data)
                print(f"Successfully migrated {filepath} to MongoDB.")
            except Exception as e:
                print(f"Error migrating {filepath}: {e}")
        else:
            print(f"Skipping {filepath} (file does not exist, initializing empty collection).")
            write_json(filepath, default_val)
            
    print("Migration finished!")

if __name__ == '__main__':
    migrate()
