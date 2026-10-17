import requests

base_url = "https://leon4gr45-agent-0.hf.space"
token = "Elzheimer"
headers = {"Authorization": f"Bearer {token}"}

res = requests.post(f"{base_url}/v1/ocr/page", headers=headers, files={"file": open("test_image.png", "rb")}, data={"sensitivity": "D0"})
print(res.status_code)
print(res.text)
