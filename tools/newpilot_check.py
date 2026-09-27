import json, urllib.request
BASE = "http://127.0.0.1:8001"
tok = json.load(urllib.request.urlopen(urllib.request.Request(
    BASE + "/api/v1/auth/login",
    data=json.dumps({"username": "reviewer", "password": "reviewer123"}).encode(),
    headers={"Content-Type": "application/json"}), timeout=30))["token"]
st = json.load(urllib.request.urlopen(urllib.request.Request(
    BASE + "/api/v1/system/status",
    headers={"Authorization": "Bearer " + tok}), timeout=30))
print("app:", st["app"]["name"], st["app"]["version"])
print("docs:", st["counts"]["documents"], "records:", st["counts"]["records"])
print("ocr:", st["ocr"]["tesseract_available"], st["ocr"]["installed_languages"])
print("llm:", st.get("llm", {}).get("enabled"), st.get("llm", {}).get("model"))
print("nlp:", st.get("nlp"))
