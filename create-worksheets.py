import os
import csv
import pypdf
import pymupdf
import datetime
import io
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.graphics import shapes
from ids import ids

suffix = ''
pdf_name = 'm-all-2024.11' + suffix + '.pdf'
pdfReader = pypdf.PdfReader(pdf_name)
mymuPdfReader = pymupdf.open(pdf_name)

cats = []

for id in ids:
    if id['cat'] not in cats:
        cats.append(id['cat'])

# if relevant id object does not exist in ids.py

# data = csv.reader(open('m-safe-2024.11.csv'), delimiter=',', skipinitialspace=True)
# ids, cats, skills = [], [], []

# for row in data:
#     # worksheet ids
#     ids.append({
#         'id': row[0],
#         'cat': row[1],
#         'page': None,
#         'page2': False,
#     })

#     # test ids
#     # ids.append({
#     #     'id': row[0],
#     #     'cat': row[1],
#     #     'skill': row[2],
#     #     'page': None,
#     #     'page2': False,
#     # })

#     # if row[2] not in skills:
#     #     skills.append(row[2])

#     if row[1] not in cats:
#         cats.append(row[1])

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

### Create PDFs
one_line_cats = [
    'Transitions',
    'Words in Context',
    'Linear Functions',
    'Nonlinear Functions'
    ]
two_line_cats = [
    'Text, Structure, and Purpose',
    'Cross-Text Connections',
    'Linear Equations in Two Variables',
    'Area and Volume',
    'Right Triangles and Trigonometry',
    'Circles',
    'Lines, Angles, and Triangles',
    'Linear Equations in One Variable',
    'Percentages',
    'Boundaries',
    'Form, Structure, and Sense',
    'Rhetorical Synthesis',
    'Command of Evidence',
    'Central Ideas and Details',
    'Inferences'
    ]
three_line_cats = [
    'Models and Scatterplots',
    'Probability',
    'Sample Statistics and Margin of Error',
    'Systems of Linear Equations',
    'Equivalent Expressions',
    'Linear Inequalities',
    'Sample Statistics and Margin of Error'
    ]
four_line_cats = [
    'Ratios, Rates, Proportions, and Units',
    'Distributions'
    ]
five_line_cats = [
    'Nonlinear Equations and Systems',
    'Observational Studies and Experiments'
    ]

for cat in cats:
    # folder = "Math 2024.11/" + cat[0:len(cat)-2] # worksheets
    # folder = "Math 2024.11~Key" # worksheet answer keys
    folder = "SAT new" # practice tests
    if not os.path.exists(folder):
        os.makedirs(folder)
    qNum = 1
    pdfWriter = pypdf.PdfWriter()

    # worksheets
    # if cat[:-2] in one_line_cats:
    #     heightMod = 630 + 12
    # elif cat[:-2] in three_line_cats:
    #     heightMod = 630 - 12
    # elif cat[:-2] in four_line_cats:
    #     heightMod = 630 - 24
    # elif cat[:-2] in five_line_cats:
    #     heightMod = 630 - 35
    # elif cat[:-2] in two_line_cats:
    #     heightMod = 630
    # else:
    #     heightMod = 630
    #     print(cat + " not found")


    for id in ids:
        # practice tests
        if id['skill'] in one_line_cats:
            heightMod = 635 + 12
        elif id['skill'] in three_line_cats:
            heightMod = 635 - 12
        elif id['skill'] in four_line_cats:
            heightMod = 635 - 24
        elif id['skill'] in five_line_cats:
            heightMod = 635 - 35
        elif id['skill'] in two_line_cats:
            heightMod = 635
        else:
            heightMod = 635
            print(id['skill'] + " not found")
        if id['cat'] == cat:
            print(datetime.datetime.now().strftime(format='%H:%M'), id['id'], id['cat'])
            packet = io.BytesIO()
            can = canvas.Canvas(packet, pagesize=letter)
            qId = id['cat'][len(id['cat'])-1] + "." + str(qNum)
            tId = id['cat'] + "." + str(qNum)
            can.setFillColor('white')
            can.rect(500, heightMod - 3, 100, 20, stroke=0, fill=1)
            can.setFillColor('black')
            # can.drawString(558, heightMod, qId) # concept worksheets below header
            can.drawString(502, heightMod, tId) # practice tests below header
            #can.drawString(558, 748, qId) # concept worksheets above header
            can.save()

            #move to the beginning of the StringIO buffer
            packet.seek(0)

            # create a new PDF with Reportlab
            new_pdf = pypdf.PdfReader(packet)
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
            if id['page2'] and id['page'] < len(pdfReader.pages) - 1:
                nextPageObj = pdfReader.pages[id['page'] + 1]
                pdfWriter.add_page(nextPageObj)
                print("page 2 added")

            qNum += 1

    pdfOutput = open(folder + "/" + cat + suffix + '.pdf', 'wb')
    pdfWriter.write(pdfOutput)
    pdfOutput.close()
    print(cat + suffix + " complete!")
print("worksheets complete!")


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
#     pdfWriter = pypdf.PdfWriter()
#     for id in ids:
#         if id['cat'] == cat:
#             packet = io.BytesIO()
#             can = canvas.Canvas(packet, pagesize=letter)
#             can.drawString(520, 742, id['code'])
#             can.save()
#             #move to the beginning of the StringIO buffer
#             packet.seek(0)

#             # create a new PDF with Reportlab
#             new_pdf = pypdf.PdfReader(packet)
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