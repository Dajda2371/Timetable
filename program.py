import json
import random
import platform

def load_config(file_path):
    with open(file_path, "r") as f:
        return json.load(f)

def generate_timetable(config, days, periods_per_day):
    teachers = {t["name"]: t["subjects"] for t in config["teachers"]}
    classes = config["classes"]
    time_grant = config["time_grant"]
    
    timetable = {teacher: {} for teacher in teachers}
    class_timetable = {cls["class_name"]: {} for cls in classes}
    
    assigned_hours = {cls["class_name"]: {subj: 0 for subj in time_grant[str(cls["grade"])]} for cls in classes}
    subject_teachers = {}
    
    for cls in classes:
        class_name = cls["class_name"]
        class_teacher = cls["class_teacher"]
        grade = str(cls["grade"])
        
        for subject in time_grant[grade]:
            if subject in teachers[class_teacher]:
                subject_teachers.setdefault(class_name, {})[subject] = class_teacher
            else:
                available_teachers = [t for t, subs in teachers.items() if subject in subs]
                if available_teachers:
                    subject_teachers.setdefault(class_name, {})[subject] = random.choice(available_teachers)
    
    for day in days:
        for period in range(1, periods_per_day + 1):
            available_teachers = set(teachers.keys())
            available_classes = set(cls["class_name"] for cls in classes)
            
            for cls in classes:
                class_name = cls["class_name"]
                grade = str(cls["grade"])
                
                if class_name not in available_classes:
                    continue
                
                subjects_needed = [s for s, h in time_grant[grade].items() if assigned_hours[class_name][s] < h]
                
                if not subjects_needed:
                    continue
                
                subject = random.choice(subjects_needed)
                teacher = subject_teachers[class_name][subject]
                
                if teacher not in available_teachers:
                    continue
                
                timetable[teacher].setdefault(day, {})[period] = {"class": class_name, "subject": subject}
                class_timetable[class_name].setdefault(day, {})[period] = {"teacher": teacher, "subject": subject}
                
                assigned_hours[class_name][subject] += 1
                available_teachers.remove(teacher)
                available_classes.remove(class_name)
    
    return timetable, class_timetable

# Load configuration
config = load_config("config.json")

days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
periods_per_day = 8

timetable, class_timetable = generate_timetable(config, days, periods_per_day)

data = {
    "teachers_timetable": timetable,
    "classes_timetable": class_timetable
}

# Save to JSON file
if platform.system() == "Windows":
    with open("data\\timetable.json", "w") as f:
        json.dump(data, f, indent=4)
        print("Timetable generated and saved to \"data\\timetable.json\"")
else:
    with open("data/timetable.json", "w") as f:
        json.dump(data, f, indent=4)
        print("Timetable generated and saved to \"data/timetable.json\"")