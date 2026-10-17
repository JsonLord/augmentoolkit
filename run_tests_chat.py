import requests

base_url = "https://leon4gr45-agent-0.hf.space"
token = "Elzheimer"
headers = {"Authorization": f"Bearer {token}"}

with open("base64_image.txt", "r") as f:
    b64 = f.read().strip()

print("9. POST /v1/ocr/page")
files = {"file": open("test_image.png", "rb")}
data = {"sensitivity": "D0"}
print(requests.post(f"{base_url}/v1/ocr/page", headers=headers, files=files, data=data).json().get("text", "No text"))


req_data = {
    "model": "ocr-cpu",
    "messages": [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"}
                }
            ]
        }
    ]
}
print("10. POST /v1/chat/completions")
res = requests.post(f"{base_url}/v1/chat/completions", headers=headers, json=req_data)
if res.status_code == 200:
    print(res.json()["choices"][0]["message"]["content"])
else:
    print(res.status_code)
    print(res.text)

print("11. internal capabilities")
print(requests.get(f"{base_url}/internal/capabilities", headers=headers).status_code)
