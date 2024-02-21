import os
import csv
import PyPDF2
import datetime
import io
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from ids import ids


pdfFileObj = open('rw-questions.pdf', 'rb')
pdfReader = PyPDF2.PdfReader(pdfFileObj)

### if relevant id object does not exist in ids.py

data = csv.reader(open('rw-all-but-sat.csv'), delimiter=',', skipinitialspace=True) 
ids, cats = [], []

for row in data:
    ids.append({
        'id': row[0],
        'cat': row[1],
        'page': None,
        'page2': False,
    })

    if row[1] not in cats:
        cats.append(row[1])

for p in range(len(pdfReader.pages)):
    pageObj = pdfReader.pages[p]
    pageText = pageObj.extract_text()
    if p < len(pdfReader.pages) - 1:
        nextPageObj = pdfReader.pages[p + 1]
    nextText = nextPageObj.extract_text()
    for id in ids:
        if pageText.find("Question ID") != -1 and pageText.find(id['id']) != -1:
            id['page'] = p
            if nextText.find("Question ID") == -1:
                id['page2'] = True
            print(datetime.datetime.now().strftime(format='%H:%M'), id['id'], id['page'], id['page2'])
            break

print(ids)


### Linear test unique Qs

# data = csv.reader(open('m-uniques.csv'), delimiter=',', skipinitialspace=True) 
# # ids = []
# cats = ['M']

# for row in data:
#     ids.append({
#         'id': row[0],
#         'cat': 'M',
#         'code': row[1],
#         'page': None,
#         'page2': False,
#     })

# for p in range(len(pdfReader.pages)):
#     pageObj = pdfReader.pages[p]
#     pageText = pageObj.extract_text()
#     if p < len(pdfReader.pages) - 1:
#         nextPageObj = pdfReader.pages[p + 1]
#     nextText = nextPageObj.extract_text()
#     for id in ids:
#         if pageText.find("Question ID") != -1 and pageText.find(id['id']) != -1:
#             id['page'] = p
#             if nextText.find("Question ID") == -1:
#                 id['page2'] = True
#             print(datetime.datetime.now().strftime(format='%H:%M'), id['id'], id['page'], id['page2'])
#             break
# print(ids)

# for cat in cats:
#     folder = "LT unique questions/" + cat
#     if not os.path.exists(folder):
#         os.makedirs(folder)
#     pdfWriter = PyPDF2.PdfWriter()
#     for id in ids:
#         if id['cat'] == cat:
#             packet = io.BytesIO()
#             can = canvas.Canvas(packet, pagesize=letter)
#             can.drawString(520, 742, id['code'])
#             can.save()
#             #move to the beginning of the StringIO buffer
#             packet.seek(0)
            
#             # create a new PDF with Reportlab
#             new_pdf = PyPDF2.PdfReader(packet)
#             print(id['code'])
#             pageObj = pdfReader.pages[id['page']]
#             pageObj.merge_page(new_pdf.pages[0])

#             pdfWriter.add_page(pageObj)
#             print(datetime.datetime.now().strftime(format='%H:%M'), id['id'], id['cat'])
#             if id['page2'] and id['page'] < len(pdfReader.pages) - 1:
#                 nextPageObj = pdfReader.pages[id['page'] + 1]
#                 pdfWriter.add_page(nextPageObj)
#                 print("page 2 added")

#     pdfOutput = open(folder + "/" + cat + '.pdf', 'wb')
#     pdfWriter.write(pdfOutput)
#     pdfOutput.close()
#     print(cat + " complete!")
# print("worksheets complete!")



### If relevant id object exists in ids.py

# cats = []

# for id in ids:
#     if id['cat'] not in cats:
#         cats.append(id['cat'])

### Create PDFs

for cat in cats:
    folder = "RW PSAT safe/" + cat[0:len(cat)-2]
    if not os.path.exists(folder):
        os.makedirs(folder)
    qNum = 1
    pdfWriter = PyPDF2.PdfWriter()
    for id in ids:
        if id['cat'] == cat:
            packet = io.BytesIO()
            can = canvas.Canvas(packet, pagesize=letter)
            qId = str(qNum) # id['cat'][len(id['cat'])-1] + "." + str(qNum)
            can.drawString(558, 748, qId)
            can.save()
            #move to the beginning of the StringIO buffer
            packet.seek(0)
            
            # create a new PDF with Reportlab
            new_pdf = PyPDF2.PdfReader(packet)
            print(qId)
            if id['page'] is None:
                for i in ids:
                    if i['id'] == id['id']:
                        id['page'] = i['page']
                        id['page2'] = i['page2']
                        break
            pageObj = pdfReader.pages[id['page']]
            pageObj.merge_page(new_pdf.pages[0])

            pdfWriter.add_page(pageObj)
            print(datetime.datetime.now().strftime(format='%H:%M'), id['id'], id['cat'])
            if id['page2'] and id['page'] < len(pdfReader.pages) - 1:
                nextPageObj = pdfReader.pages[id['page'] + 1]
                pdfWriter.add_page(nextPageObj)
                print("page 2 added")
            
            qNum += 1

    pdfOutput = open(folder + "/" + cat + '.pdf', 'wb')
    pdfWriter.write(pdfOutput)
    pdfOutput.close()
    print(cat + " complete!")
print("worksheets complete!")