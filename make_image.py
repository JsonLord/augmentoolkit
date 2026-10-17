from PIL import Image, ImageDraw, ImageFont
import base64

img = Image.new('RGB', (400, 100), color = (255, 255, 255))
d = ImageDraw.Draw(img)
# Just write some text
d.text((10,10), "OCR AGENT TEST 2026", fill=(0,0,0))
img.save('test_image.png')

with open("test_image.png", "rb") as image_file:
    encoded_string = base64.b64encode(image_file.read()).decode("utf-8")

with open("base64_image.txt", "w") as text_file:
    text_file.write(encoded_string)
