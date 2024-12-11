import json

questions = []

domains = [
  'Information & Ideas',
  'Craft & Structure',
  'Algebra',
  'Advanced Math',
  'Geometry & Trigonometry',
  'Problem-Solving & Data Analysis',
  'Standard English Conventions',
  'Expression of Ideas'
]

skills = [
  'Inferences',
  'Central Ideas and Details',
  'Command of Evidence',
  'Words in Context',
  'Text, Structure, and Purpose',
  'Cross-Text Connections',
  'Linear Equations in Two Variables',
  'Nonlinear Equations and Systems',
  'Nonlinear Functions',
  'Area and Volume',
  'Right Triangles and Trigonometry',
  'Equivalent Expressions',
  'Ratios, Rates, Proportions, and Units',
  'Circles',
  'Lines, Angles, and Triangles',
  'Linear Functions',
  'Linear Equations in One Variable',
  'Percentages',
  'Distributions',
  'Linear Inequalities',
  'Models and Scatterplots',
  'Systems of Linear Equations',
  'Probability',
  'Sample Statistics and Margin of Error',
  'Boundaries',
  'Form, Structure, and Sense',
  'Transitions',
  'Rhetorical Synthesis',
  'Observational Studies and Experiments'
]

for line in reversed(list(open("newM.txt"))):
  if len(line) < 50:
    line = line.rstrip() # remove trailing whitespace
    if(line[0:15] == "Correct Answer:"):
      answer = line[16:]
    # elif(line[0:21] == "The correct answer is" and answer is None):
    #   end = line.find(". ")
    #   answer = line[22:end]
    elif(line in domains):
      domain = line
    elif(line in skills):
      skill = line
    elif(line[0:20] == "Question Difficulty:"):
      difficulty = line[21:]
    elif(line[0:11] == "Question ID"):
      id = line[12:]
      print(id)
      questions.append({
        'id': id,
        'answer': answer or None,
        'domain': domain or None,
        'skill': skill or None,
        "difficulty": difficulty or None
      })
      id = None
      answer = None
      domain = None
      skill = None
      difficulty = None

with open('m-new.json', 'w', encoding='utf-8') as rw:
  json.dump(questions, rw, ensure_ascii=False, indent=2)