import os, requests, sys
key = os.environ.get('GEMINI_API_KEY', '')
if not key:
    print('ERROR: GEMINI_API_KEY not set')
    sys.exit(1)
url = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=' + key
payload = {'contents': [{'parts': [{'text': 'Say hello in one word'}]}]}
r = requests.post(url, json=payload, timeout=30)
print('HTTP status:', r.status_code)
if r.status_code == 200:
    text = r.json()['candidates'][0]['content']['parts'][0]['text']
    print('Response:', text.strip())
    print('GEMINI KEY: VALID')
else:
    print('Error:', r.text[:300])
    print('GEMINI KEY: FAILED')
    sys.exit(1)
