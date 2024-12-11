import fitz  # PyMuPDF
from PIL import Image, ImageChops, ImageEnhance, ImageFilter
import pytesseract
import re

# Path to your PDF file
pdf_path = 'rw-all-2024.11.pdf'  # replace with your PDF file path

# Open the PDF file
doc = fitz.open(pdf_path)

# Function to preprocess the image to improve OCR accuracy
def preprocess_image(img):
    # Convert to grayscale
    img = img.convert('L')
    # Apply a filter to sharpen the image
    img = img.filter(ImageFilter.SHARPEN)
    # Enhance the contrast
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2)
    return img

# Function to extract the Question ID from an image
def extract_question_id(img):
    # Preprocess the image
    img = preprocess_image(img)
    # Use OCR to extract text from the image
    ocr_result = pytesseract.image_to_string(img)
    # Use regex to find the question ID pattern
    match = re.search(r'Question ID[:\s]*([A-Za-z0-9]+)', ocr_result)
    if match:
        return match.group(1)
    return None

# Function to find the vertical position of "ID:" text
def find_id_position(img):
    img = preprocess_image(img)
    ocr_result = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    for i, text in enumerate(ocr_result['text']):
        if text.startswith('ID:'):
            return ocr_result['top'][i] + ocr_result['height'][i]  # return the bottom y-coordinate of "ID:" text
    return None

# Function to trim white space from the image
def trim_white_space(image):
    bg = Image.new(image.mode, image.size, image.getpixel((0,0)))
    diff = ImageChops.difference(image, bg)
    bbox = diff.getbbox()
    if bbox:
        return image.crop((0, 0, image.width, bbox[3] + 30))
    return image

# Iterate through each page
for page_num in range(len(doc)):
    page = doc.load_page(page_num)

    # Increase resolution when rendering the page to an image
    zoom = 3  # zoom factor to increase resolution
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

    # Extract the Question ID from the image
    question_id = extract_question_id(img)
    if not question_id:
        question_id = f'question_{page_num + 1:04d}'  # fallback if ID is not found

    # Find the position of the "ID:" text
    id_position = find_id_position(img)
    if id_position:
        id_position = id_position / 3 + 8 # 8.48 for no/minimal Math topline, 8 for topline
    else:
        id_position = 0  # Fallback top margin if "ID:" position is not found

    print(f'Page {page_num + 1}: ID Position - {id_position}')

    # Define the region to exclude everything above the "ID:" text
    rect = fitz.Rect(0, id_position, pix.width, pix.height)

    # Render the defined region to an image at high resolution
    question_pix = page.get_pixmap(matrix=mat, clip=rect)
    question_img = Image.frombytes("RGB", [question_pix.width, question_pix.height], question_pix.samples)

    # Trim the white space from the bottom
    question_img = trim_white_space(question_img)

    # Save the image as a JPG file
    question_img.save(f'_rw/_{question_id}.jpg', 'JPEG', subsampling=0,quality=80)

    print(f'Saved: {question_id}.jpg')

print("All questions have been extracted and saved as JPG images.")
