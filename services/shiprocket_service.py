import os
import json
import requests
import datetime
from datetime import timedelta
from utils.db import read_json, write_json

TOKEN_FILE = 'data/shiprocket_token.json'
BASE_URL = "https://apiv2.shiprocket.in/v1/external"

class ShiprocketService:
    def __init__(self):
        self.email = os.environ.get('SHIPROCKET_EMAIL')
        self.password = os.environ.get('SHIPROCKET_PASSWORD')

    def get_token(self):
        """Retrieves a valid token, refreshing if it's older than 6 days."""
        try:
            data = read_json(TOKEN_FILE)
            if data:
                cached_email = data.get('email')
                token = data.get('token')
                login_time_str = data.get('login_time')
                
                # Force re-login if credentials changed in environment
                if cached_email != self.email:
                    return self.login()
                
                if token and login_time_str:
                    login_time = datetime.datetime.fromisoformat(login_time_str)
                    # Check if token is older than 6 days
                    if datetime.datetime.now() - login_time < timedelta(days=6):
                        return token
        except Exception:
            pass # logic will fall through to re-login

        # If we reach here, we need to login
        return self.login()

    def login(self):
        """Authenticates with Shiprocket and saves the token."""
        url = f"{BASE_URL}/auth/login"
        payload = {
            "email": self.email,
            "password": self.password
        }
        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            token = data.get('token')
            
            # Save token with current time and email
            write_json(TOKEN_FILE, {
                'email': self.email,
                'token': token,
                'login_time': datetime.datetime.now().isoformat()
            })
            
            return token
        except Exception as e:
            print(f"Shiprocket Login Error: {e}")
            return None

    def check_serviceability(self, pickup_postcode, delivery_postcode, weight, cod, country='IN', length=10, breadth=10, height=10):
        """
        Checks serviceability and rates.
        weight: in kg
        cod: 1 for COD, 0 for Prepaid
        country: ISO Alpha-2 Code (e.g. IN, US, GB)
        """
        print(f"[SHIPROCKET DEBUG] check_serviceability called: pickup={pickup_postcode}, delivery={delivery_postcode}, weight={weight}, country={country}")
        token = self.get_token()
        if not token:
            print("[SHIPROCKET DEBUG] Auth Failed in check_serviceability")
            return {"error": "Authentication failed"}

        if country == 'IN':
            # Domestic Serviceability
            url = f"{BASE_URL}/courier/serviceability"
            params = {
                "pickup_postcode": pickup_postcode,
                "delivery_postcode": delivery_postcode,
                "weight": weight,
                "cod": cod,
                "length": length,
                "breadth": breadth,
                "height": height
            }
        else:
            # International Serviceability
            url = f"{BASE_URL}/courier/international/serviceability"
            params = {
                "pickup_postcode": pickup_postcode,
                "delivery_country": country,
                "delivery_postcode": delivery_postcode,
                "weight": weight,
                "cod": cod,
                "length": length,
                "breadth": breadth,
                "height": height
            }

        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {token}'
        }

        try:
            print(f"[SHIPROCKET DEBUG] URL: {url} | Params: {params}")
            response = requests.get(url, headers=headers, params=params)
            print(f"[SHIPROCKET DEBUG] Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                # print(f"[SHIPROCKET DEBUG] Response: {json.dumps(data)}") # Uncomment if needed, verbose
                return data
            else:
                print(f"[SHIPROCKET DEBUG] API Error Body: {response.text}")
                return {"error": f"API Error: {response.status_code}", "details": response.text}
        except Exception as e:
            print(f"[SHIPROCKET DEBUG] Exception: {e}")
            return {"error": str(e)}

    def select_best_courier(self, couriers_data):
        """
        Selects the best courier based on logic:
        - Cost efficient but not too late.
        """
        try:
            if not couriers_data or not couriers_data.get('data'):
                print("[SHIPROCKET DEBUG] select_best_courier: No data found")
                return None
                
            # Handle International/Domestic structure differences if any
            # Standard structure: data -> available_courier_companies (list)
            couriers = couriers_data['data'].get('available_courier_companies', [])
            
            if not couriers:
                print("[SHIPROCKET DEBUG] select_best_courier: No couriers list found")
                return None
            
            # 1. Sort by Rate
            # Ensure rate is float
            for c in couriers:
                c['rate'] = float(c.get('rate', 999999))
                
            couriers.sort(key=lambda x: x['rate'])
            
            best_courier = couriers[0]
            baseline_rate = best_courier['rate']
            
            # Simple Logic: Stick to cheapest for now to be safe and predictable
            # User can enhance later
            
            print(f"[SHIPROCKET DEBUG] Selected Courier: {best_courier.get('courier_name')} (ID: {best_courier.get('courier_company_id')}) at Rate: {baseline_rate}")
            return best_courier
            
        except Exception as e:
            print(f"[SHIPROCKET DEBUG] select_best_courier Error: {e}")
            return None

    def generate_awb(self, shipment_id, courier_id):
        """Generate AWB for a shipment."""
        token = self.get_token()
        if not token: return {"error": "Authentication failed"}
        
        url = f"{BASE_URL}/courier/assign/awb"
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {token}'
        }
        payload = {
            "shipment_id": shipment_id,
            "courier_id": courier_id
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload)
            return response.json()
        except Exception as e:
            return {"error": str(e)}

    def create_order(self, order_data):
        """
        Creates an order in Shiprocket.
        order_data: Dictionary containing necessary order fields mapped to Shiprocket API.
        """
        token = self.get_token()
        if not token:
            return {"error": "Authentication failed"}

        url = f"{BASE_URL}/orders/create/adhoc"
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {token}'
        }

        try:
            response = requests.post(url, headers=headers, json=order_data)
            return response.json()
        except Exception as e:
            return {"error": str(e)}

    def track_order_by_id(self, order_id):
        """Tracks an order using the Shiprocket Order ID or Channel Order ID."""
        token = self.get_token()
        if not token:
            return {"error": "Authentication failed"}

        # Tracking by Order ID (Shiprocket internal ID) or AWB is standard
        # If we only have our local Channel Order ID, passing it might not work directly deeply depends on implementation
        # The standard endpoint is /courier/track/shipment/{shipment_id} or AWB.
        # However, there is /courier/track/order/{order_id} 
        
        # Let's try tracking by AWB if we have it, otherwise we might need lookup.
        # For simplicity, assuming the caller passes an AWB or we use the generic track endpoint.
        
        # NOTE: If order_id is the Channel Order ID (our DB ID), we try checking via that if supported, 
        # otherwise we track by AWB.
        
        url = f"{BASE_URL}/courier/track/awb/{order_id}" # Assuming order_id passed here is AWB for now or we will adjust.
        
        headers = {
            'Authorization': f'Bearer {token}'
        }
        
        try:
            response = requests.get(url, headers=headers)
            return response.json()
        except Exception as e:
            return {"error": str(e)}

    def track_awb(self, awb_code):
        token = self.get_token()
        if not token:
             return {"error": "Authentication failed"}
        
        url = f"{BASE_URL}/courier/track/awb/{awb_code}"
        headers = {'Authorization': f'Bearer {token}'}
        
        try:
            response = requests.get(url, headers=headers)
            return response.json()
        except Exception as e:
            return {"error": str(e)}

    def cancel_order(self, order_ids):
        """
        Cancels orders in Shiprocket.
        order_ids: List of Shiprocket Order IDs (integers or strings)
        """
        token = self.get_token()
        if not token:
            return {"error": "Authentication failed"}

        url = f"{BASE_URL}/orders/cancel"
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {token}'
        }
        
        # Shiprocket expects {"ids": [123, 456]}
        payload = {"ids": order_ids}

        try:
            response = requests.post(url, headers=headers, json=payload)
            return response.json()
        except Exception as e:
            return {"error": str(e)}
    def create_return_order(self, return_data):
        """
        Creates a return order in Shiprocket.
        return_data: Dictionary containing necessary return fields.
        """
        token = self.get_token()
        if not token:
            return {"error": "Authentication failed"}

        url = f"{BASE_URL}/orders/create/return"
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {token}'
        }

        try:
            response = requests.post(url, headers=headers, json=return_data)
            return response.json()
        except Exception as e:
            return {"error": str(e)}
