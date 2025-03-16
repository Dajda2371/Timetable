import webbrowser
import json
import random
import http.server
import platform
import socketserver

def run_ui():
    webbrowser.open("http://localhost:8000/ui.html")

def load_config(file_path):
    with open(file_path, "r") as f:
        return json.load(f)

def generate_timetable(config):
    teachers = {t["name"]: t["subjects"] for t in config["teachers"]}
    classes = config["classes"]
    time_grant = config["time_grant"]
    schedule_config = config["schedule_config"]
    
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
    
    class_lunch_schedules = {cls["class_name"]: set() for cls in classes}
    teacher_lunch_schedules = {teacher: set() for teacher in teachers}
    
    for day, config in schedule_config.items():
        max_periods = config["max_periods"]
        lunch_breaks = config["lunch_breaks"]
        available_classes = set(cls["class_name"] for cls in classes)
        
        # Distribute lunch breaks evenly across classes
        class_list = list(available_classes)
        random.shuffle(class_list)
        for i, lunch_period in enumerate(lunch_breaks):
            for j in range(i, len(class_list), len(lunch_breaks)):
                class_lunch_schedules[class_list[j]].add(lunch_period)
        
        # Distribute lunch breaks evenly across teachers
        teacher_list = list(teachers.keys())
        random.shuffle(teacher_list)
        for i, lunch_period in enumerate(lunch_breaks):
            for j in range(i, len(teacher_list), len(lunch_breaks)):
                teacher_lunch_schedules[teacher_list[j]].add(lunch_period)
        
        for period in range(1, max_periods + 1):
            available_teachers = set(teachers.keys())
            
            for cls in classes:
                class_name = cls["class_name"]
                grade = str(cls["grade"])
                
                if class_name not in available_classes or period in class_lunch_schedules[class_name]:
                    continue
                
                subjects_needed = [s for s, h in time_grant[grade].items() if assigned_hours[class_name][s] < h]
                
                if not subjects_needed:
                    continue
                
                subject = random.choice(subjects_needed)
                teacher = subject_teachers[class_name][subject]
                
                if teacher not in available_teachers or period in teacher_lunch_schedules[teacher]:
                    continue
                
                timetable[teacher].setdefault(day, {})[period] = {"class": class_name, "subject": subject}
                class_timetable[class_name].setdefault(day, {})[period] = {"teacher": teacher, "subject": subject}
                
                assigned_hours[class_name][subject] += 1
                available_teachers.remove(teacher)
    
    return timetable, class_timetable

class RequestHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/generate":
            config = load_config("config.json")
            timetable, class_timetable = generate_timetable(config)
            data = {
                "teachers_timetable": timetable,
                "classes_timetable": class_timetable
            }

            # Save to JSON file
            if platform.system() == "Windows":
                with open("data\\timetable.json", "w") as f:
                    json.dump(data, f, indent=4)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"message": "Timetable saved to \"data\\timetable.json\""}).encode())
                    print("Timetable generated and saved to \"data\\timetable.json\"")
            else:
                with open("data/timetable.json", "w") as f:
                    json.dump(data, f, indent=4)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"message": "Timetable saved to \"data/timetable.json\""}).encode())
                    print("Timetable generated and saved to \"data/timetable.json\"")
        else:
            super().do_GET()

PORT = 8000
with socketserver.TCPServer(("", PORT), RequestHandler) as httpd:
    run_ui()
    print(f"Serving at port {PORT}")
    httpd.serve_forever()
