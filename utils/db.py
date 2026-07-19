import os
import pymongo
from pymongo import MongoClient

MONGO_URI = os.environ.get('MONGO_URI') or 'mongodb://localhost:27017/huba2z'
try:
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
    mongo_client.server_info()
    mongo_db = mongo_client.get_database()
    print("[MONGODB] Connected successfully!")
except Exception as e:
    print(f"[MONGODB] Connection failed: {e}.")
    mongo_db = None

def get_collection_info(filepath):

    filename = os.path.basename(filepath)
    if filename == 'users.json':
        return 'users', True
    elif filename == 'orders.json':
        return 'orders', True
    elif filename == 'ratings.json':
        return 'ratings', True
    elif filename == 'reports.json':
        return 'reports', True
    elif filename == 'wallet_transactions.json':
        return 'wallet_transactions', True
    elif filename == 'notifications.json':
        return 'notifications', True
    elif filename == 'coupons.json':
        return 'coupons', True
    elif filename == 'products.json':
        return 'products', True
    elif filename == 'otps.json':
        return 'otps', False
    elif filename == 'content.json':
        return 'content', False
    elif filename == 'shiprocket_token.json':
        return 'shiprocket_token', False
    return None, None

def read_json(filepath):
    if mongo_db is None:
        raise RuntimeError("MongoDB connection is not active. This application requires a running MongoDB database.")

    coll_name, is_array = get_collection_info(filepath)
    if coll_name is None:
        return [] if is_array else {}

    coll = mongo_db[coll_name]
    if is_array:
        cursor = coll.find({}, {'_id': 0})
        if coll.count_documents({'__order_index': {'$exists': True}}) > 0:
            cursor = cursor.sort('__order_index', pymongo.ASCENDING)
        
        data = []
        for doc in cursor:
            doc.pop('__order_index', None)
            data.append(doc)
        return data
    else:
        doc = coll.find_one({'_id': 'main_data'}, {'_id': 0})
        if doc:
            return doc.get('data', {})
        return {}

def write_json(filepath, data):
    if mongo_db is None:
        raise RuntimeError("MongoDB connection is not active. This application requires a running MongoDB database.")

    coll_name, is_array = get_collection_info(filepath)
    if coll_name is None:
        return

    coll = mongo_db[coll_name]
    if is_array:
        if not isinstance(data, list):
            data = []
        
        coll.create_index('id', unique=True)
        
        ids = []
        for index, item in enumerate(data):
            if isinstance(item, dict) and 'id' in item:
                item_id = item['id']
                ids.append(item_id)
                
                item_copy = item.copy()
                item_copy.pop('_id', None)
                item_copy['__order_index'] = index
                
                coll.replace_one({'id': item_id}, item_copy, upsert=True)
        
        coll.delete_many({'id': {'$nin': ids}})
    else:
        if not isinstance(data, dict):
            data = {}
        coll.replace_one({'_id': 'main_data'}, {'_id': 'main_data', 'data': data}, upsert=True)
