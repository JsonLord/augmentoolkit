with open("ocr/http_security.py", "r") as f:
    content = f.read()

# I see:
# if not self.token: return 503
# auth = headers.get(b"authorization", b"")
# if not hmac.compare_digest(auth, expected): return 401

# If auth is b"", it shouldn't return 503 if self.token is set! It should return 401!
# But wait, why did it return 503?
