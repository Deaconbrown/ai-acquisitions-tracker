import os, requests
key = os.environ["GEMINI_API_KEY"]
print("Key present:", bool(key), "| Length:", len(key))

for version in ["v1beta", "v1"]:
    r = requests.get(f"https://generativelanguage.googleapis.com/{version}/models?key={key}", timeout=15)
    print(f"ListModels {version}: HTTP {r.status_code}")
    if r.status_code == 200:
        models = r.json().get("models", [])
        print(f"  {len(models)} models available:")
        for m in models:
            if any(k in m["name"].lower() for k in ["flash","pro","gemini"]):
                methods = m.get("supportedGenerationMethods", [])
                print(f"  {m['name']} | {methods}")
    else:
        print(f"  Error: {r.text[:300]}")
