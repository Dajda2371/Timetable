import webbrowser
import json
import random
import http.server
import platform
import socketserver
import os

if os.name == "nt":
    EXAMPLE_CONFIG_FILE = "data\\example_config.json"
    CONFIG_FILE = "data\\config.json"
    TIMETABLE_FILE = "data\\timetable.json"
    DATA_DIRECTORY = "data"
else:
    EXAMPLE_CONFIG_FILE = "data/example_config.json"
    CONFIG_FILE = "data/config.json"
    TIMETABLE_FILE = "data/timetable.json"
    DATA_DIRECTORY = "data"

def run_ui():
    # Opens the UI in a web browser.
    webbrowser.open("http://localhost:8000/ui.html")

def load_config():
    # Returns default config if the file is missing or empty/invalid.
    default_config = {
        "teachers": [],
        "classes": [],
        "time_grant": {},
        "schedule_config": {}
    }

    try:
        with open(CONFIG_FILE, "r") as f:
            data = f.read()
            if not data.strip():
                return None  # Return None if the file is empty
            return json.loads(data)
    except (FileNotFoundError, json.JSONDecodeError):
        return default_config

def save_config(data):
    # Saves the updated configuration to the JSON file.
    # Ensure the 'data' directory exists
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)

def generate_timetable(config):
    # Generates a random timetable based on the config data.
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

    for day, config in schedule_config.items():
        max_periods = config["max_periods"]
        lunch_breaks = config["lunch_breaks"]
        available_classes = set(cls["class_name"] for cls in classes)

        for period in range(1, max_periods + 1):
            available_teachers = set(teachers.keys())

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

    return timetable, class_timetable

class RequestHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        # Handles GET requests for fetching config and generating timetable.
        if self.path == "/config":
            if not os.path.exists(CONFIG_FILE):
                print("config file not found")
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"message": "Config file not found."}).encode())
                return
            try:
                config = load_config()
                if config is None:
                    print("config file is empty")
                    self.send_response(404)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"message": "Config file is empty."}).encode())
                    return
                print("config file loaded")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(config).encode())
            except Exception as e:
                self.send_error(500, "Error loading config: " + str(e))
        elif self.path == "/example-config":
            try:
                with open(EXAMPLE_CONFIG_FILE, "r") as f:
                    example_config = json.load(f)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(example_config).encode())
            except FileNotFoundError:
                self.send_error(404, "Example config file not found.")
            except Exception as e:
                self.send_error(500, "Error loading example config: " + str(e))

        elif self.path == "/generate":
            try:
                config = load_config()
                timetable, class_timetable = generate_timetable(config)
                data = {
                    "teachers_timetable": timetable,
                    "classes_timetable": class_timetable
                }

                with open(TIMETABLE_FILE, "w") as f:
                    json.dump(data, f, indent=4)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"message": "Timetable saved successfully!"}).encode())
                print("Timetable generated and saved.")
            except Exception as e:
                self.send_error(500, "Error generating timetable: " + str(e))

        else:
            # Serve files from the data directory if they are not in the root
            if not os.path.exists(self.path[1:]):
                self.path = os.path.join(DATA_DIRECTORY, self.path[1:])
                if os.path.isdir(self.path):
                    self.path = os.path.join(self.path, "index.html")
            super().do_GET()

    def do_POST(self):
        # Handles POST requests to save the updated config.json.
        if self.path == "/save-config":
            content_length = int(self.headers["Content-Length"])
            post_data = self.rfile.read(content_length)

            try:
                new_config = json.loads(post_data.decode("utf-8"))
                save_config(new_config)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"message": "Config saved successfully!"}).encode())
                print("Config updated successfully.")
            except Exception as e:
                self.send_error(500, "Error saving config: " + str(e))

# Change the directory before starting the server
os.chdir(os.path.dirname(os.path.abspath(__file__)))

PORT = 8000
with socketserver.TCPServer(("", PORT), RequestHandler) as httpd:
    run_ui()
    print(f"Server running at http://localhost:{PORT}")
    httpd.serve_forever()
    print("Server stopped.")