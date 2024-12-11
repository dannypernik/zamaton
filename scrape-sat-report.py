import PyPDF2

pages_text=[]
with open(r"newM.pdf", 'rb') as pdfFileObject:
    pdfReader = PyPDF2.PdfReader(pdfFileObject)

    for page in range(len(pdfReader.pages)):
        pageObject = pdfReader.pages[page]
        pages_text.append(pageObject.extract_text())

lines = pages_text
with open('newM.txt', 'w') as f:
  for line in lines:
    f.write(line)
    f.write('\n')

# print(pages_text)