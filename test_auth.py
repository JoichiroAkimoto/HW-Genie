import json
import requests
import time

# Load session.json
with open('session.json', 'r') as f:
    session = json.load(f)

headers = session['headers']
headers['Content-Type'] = 'application/json; charset=UTF-8'
headers['User-Agent'] = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36'

url = "https://heroes-wb.nextersglobal.com/api/"
payload = {"calls": [{"name": "userGetInfo", "args": {}, "context": {"actionTs": int(time.time())}, "ident": "body"}]}

try:
    response = requests.post(url, headers=headers, json=payload, timeout=15)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.text}")
except Exception as e:
    print(f"Error: {e}")
