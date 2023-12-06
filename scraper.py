import json

questions = []

for line in reversed(list(open("m-all.txt"))):
  if(line[0:15] == "Correct Answer:"):
    answer = line[16:-1]
  elif(line[0:21] == "The correct answer is" and answer == None):
    end = line.find(". ")
    answer = line[22:end]
  elif(line[0:6] == "Domain"):
    domain = line[7:-1]
  elif(line[0:20] == "Question Difficulty:"):
    difficulty = line[21:-1]
  elif(line[0:5] == "Skill"):
    skill = line[6:-1]
  elif(line[0:11] == "Question ID"):
    id = line[12:-1]
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

with open('m-all.json', 'w', encoding='utf-8') as rw:
  json.dump(questions, rw, ensure_ascii=False, indent=2)