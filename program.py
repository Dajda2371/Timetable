import json
import random

def generate_timetable(teachers, classes, days, periods_per_day):
    timetable = {teacher: {} for teacher in teachers}
    class_timetable = {cls: {} for cls in classes}
    
    for day in days:
        for period in range(1, periods_per_day + 1):
            available_teachers = set(teachers)
            available_classes = set(classes)
            
            for cls in classes:
                if cls not in available_classes:
                    continue
                
                possible_teachers = [t for t in available_teachers if t in teachers]
                
                if not possible_teachers:
                    continue
                
                teacher = random.choice(possible_teachers)
                
                timetable[teacher].setdefault(day, {})[period] = cls
                class_timetable[cls].setdefault(day, {})[period] = teacher
                
                available_teachers.remove(teacher)
                available_classes.remove(cls)
    
    return timetable, class_timetable

# Define teachers and classes
teachers = ["Mr. Smith", "Ms. Johnson", "Mr. Brown", "Ms. Garcia"]
classes = ["Class 1A", "Class 1B", "Class 2A", "Class 2B"]

days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
periods_per_day = 6

timetable, class_timetable = generate_timetable(teachers, classes, days, periods_per_day)

data = {
    "teachers_timetable": timetable,
    "classes_timetable": class_timetable
}

# Save to JSON file
with open("timetable.json", "w") as f:
    json.dump(data, f, indent=4)

print("Timetable generated and saved to timetable.json")
