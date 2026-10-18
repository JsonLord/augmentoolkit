with open("ocr/worker_api.py", "r") as f:
    text = f.read()

# I see the user has `OPENAI_COMPATIBLE_API` set as a secret, not `OPENAI_COMPATIBLE_KEY`!
# Let's change `OPENAI_COMPATIBLE_KEY` to `OPENAI_COMPATIBLE_API`
text = text.replace('openai_key = os.getenv("OPENAI_COMPATIBLE_KEY")', 'openai_key = os.getenv("OPENAI_COMPATIBLE_API")')

with open("ocr/worker_api.py", "w") as f:
    f.write(text)
