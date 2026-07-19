import os
import base64
import hashlib
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

def load_key(enc_type='general'):
    key_map = {
        'phone': 'PHONE_ENCRYPTION_KEY',
        'address': 'ADDRESS_ENCRYPTION_KEY',
        'general': 'ENCRYPTION_KEY'
    }
    env_var = key_map.get(enc_type, 'ENCRYPTION_KEY')
    key = os.environ.get(env_var)
    
    if not key and enc_type != 'general':
         key = os.environ.get('ENCRYPTION_KEY')

    if not key:
        raise ValueError(f"{env_var} not found in environment variables.")
    return key.encode() if isinstance(key, str) else key

def encrypt_data(plaintext, enc_type='general'):
    if not plaintext:
        return None
    key = load_key(enc_type)
    f = Fernet(key)
    if isinstance(plaintext, str):
        plaintext = plaintext.encode()
    return f.encrypt(plaintext).decode('utf-8')

def decrypt_data(ciphertext, enc_type='general'):
    if not ciphertext:
        return None
    key = load_key(enc_type)
    f = Fernet(key)
    if isinstance(ciphertext, str):
        ciphertext = ciphertext.encode()
    try:
        return f.decrypt(ciphertext).decode('utf-8')
    except Exception as e:
        print(f"Decryption Error ({enc_type}): {e}")
        return None

def hash_data(text):
    if not text:
        return None
    return hashlib.sha256(text.encode()).hexdigest()

def hash_otp(otp):
    if not otp:
        return None
    return hashlib.sha256(otp.encode()).hexdigest()

def verify_otp(otp, hashed_otp):
    if not otp or not hashed_otp:
        return False
    return hash_otp(otp) == hashed_otp
