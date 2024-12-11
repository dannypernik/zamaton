import os
import pypdf
import io
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.graphics import shapes

pdfReader = pypdf.PdfReader('act.pdf')
pdfWriter = pypdf.PdfWriter()

for p in range(len(pdfReader.pages)):
    packet = io.BytesIO()
    can = canvas.Canvas(packet, pagesize=letter)
    can.setFillColor('white')
    can.rect(100, 0, 400, 20, stroke=0, fill=1) # rectangle at bottom of page
    can.rect(100, 750, 400, 40, stroke=0, fill=1) # rectangle at top of page
    can.save()

    #move to the beginning of the StringIO buffer
    packet.seek(0)

    # create a new PDF with Reportlab
    new_pdf = pypdf.PdfReader(packet)

    pageObj = pdfReader.pages[p]
    pageObj.merge_page(new_pdf.pages[0])

    pdfWriter.add_page(pageObj)

pdfOutput = open('act-clean.pdf', 'wb')
pdfWriter.write(pdfOutput)
pdfOutput.close()
print("complete!")