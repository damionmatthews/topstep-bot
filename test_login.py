# test_login.py
import asyncio
import httpx
import os
import json

# --- Load environment variables (if using .env file) ---
from dotenv import load_dotenv
load_dotenv()

# --- Copy necessary global variables from main.py ---
PROJECTX_USERNAME = os.getenv("PROJECTX_USERNAME")
TOPSTEP_API_KEY = os.getenv("TOPSTEP_API_KEY")
PROJECTX_BASE_URL = "https://gateway-rtc.s2f.projectx.com/api"

projectx_session_token = None # To store the token

# --- Copy the get_projectx_token function here ---
async def get_projectx_token():
    global projectx_session_token # We'll modify this global variable

    # --- DEBUG PRINT 1: Check if variables are loaded ---
    print(f"--- Attempting ProjectX Login ---")
    print(f"Username from env: '{PROJECTX_USERNAME}'")
    print(f"API Key from env (first 5 chars): '{TOPSTEP_API_KEY[:5] if TOPSTEP_API_KEY else 'Not Set'}'") # Avoid printing full key

    if not PROJECTX_USERNAME or not TOPSTEP_API_KEY:
        print("Error: ProjectX Username or API Key not configured in environment.")
        return None

    login_url = f"{PROJECTX_BASE_URL}/Auth/loginKey"
    payload = {"userName": PROJECTX_USERNAME, "apiKey": TOPSTEP_API_KEY}

    # --- DEBUG PRINT 2: Show URL and payload ---
    print(f"Request URL: {login_url}")
    print(f"Request Payload: {json.dumps(payload)}") # Print payload as JSON string

    async with httpx.AsyncClient() as client:
        try:
            print("Sending POST request to ProjectX...")
            response = await client.post(login_url, json=payload) # httpx handles Content-Type for json

            # --- DEBUG PRINT 3: Show response status and headers ---
            print(f"Response Status Code: {response.status_code}")
            print(f"Response Headers: {response.headers}")
            
            # --- DEBUG PRINT 4: Show raw response text ---
            print(f"Raw Response Text: {response.text}")

            response.raise_for_status() # Raise an exception for HTTP error codes (4xx or 5xx)
            
            data = response.json()
            # --- DEBUG PRINT 5: Show parsed JSON response data ---
            print(f"Parsed JSON Response: {data}")

            if data.get("success") and data.get("token"):
                projectx_session_token = data["token"]
                print(f"ProjectX Login successful. Token received (first 10 chars): {projectx_session_token[:10]}...")
                return projectx_session_token
            else:
                print(f"ProjectX Login failed. Success: {data.get('success')}, ErrorCode: {data.get('errorCode')}, ErrorMessage: {data.get('errorMessage')}")
                projectx_session_token = None
                return None
        except httpx.HTTPStatusError as e:
            print(f"HTTP Error during ProjectX login: {e}")
            print(f"Response content causing HTTPError: {e.response.text}")
            projectx_session_token = None
            return None
        except httpx.RequestError as e:
            print(f"Request Error (e.g., network issue) during ProjectX login: {e}")
            projectx_session_token = None
            return None
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON response from ProjectX: {e}")
            print(f"Raw response text that failed to parse: {response.text if 'response' in locals() else 'Response object not available'}")
            projectx_session_token = None
            return None
        except Exception as e:
            print(f"An unexpected error occurred during ProjectX login: {e}")
            projectx_session_token = None
            return None

# --- Main part to run the test ---
async def main_test():
    print("Starting login test...")
    token = await get_projectx_token()
    if token:
        print(f"\nLogin Test Succeeded. Token (first 10): {token[:10]}...")
        print(f"Global 'projectx_session_token' (first 10): {projectx_session_token[:10] if projectx_session_token else 'None'}...")
    else:
        print("\nLogin Test Failed.")

if __name__ == "__main__":
    asyncio.run(main_test())
