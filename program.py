import webbrowser
import json
import random
import os
import platform
from typing import Dict, Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Body
from fastapi.responses import FileResponse, JSONResponse

# Constants
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

app = FastAPI()

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

    for day, config_day in schedule_config.items():
        max_periods = config_day["max_periods"]
        lunch_breaks = config_day.get("lunch_breaks", [])

        # Only one lunch break per day, randomly chosen from lunch_breaks
        lunch_period = random.choice(lunch_breaks) if lunch_breaks else None

        available_classes = set(cls["class_name"] for cls in classes)

        for period in range(1, max_periods + 1):
            if lunch_period and period == lunch_period:
                # Skip writing this period (lunch break)
                continue

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

@app.get("/config")
def get_config():
    if not os.path.exists(CONFIG_FILE):
        print("config file not found")
        return JSONResponse(status_code=404, content={"message": "Config file not found."})
    try:
        config = load_config()
        if config is None:
            print("config file is empty")
            return JSONResponse(status_code=404, content={"message": "Config file is empty."})
        print("config file loaded")
        return config
    except Exception as e:
        raise HTTPException(status_code=500, detail="Error loading config: " + str(e))

@app.get("/example-config")
def get_example_config():
    try:
        with open(EXAMPLE_CONFIG_FILE, "r") as f:
            example_config = json.load(f)
        return example_config
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Example config file not found.")
    except Exception as e:
        raise HTTPException(status_code=500, detail="Error loading example config: " + str(e))

@app.post("/save-config")
def save_config_endpoint(config: Dict[str, Any] = Body(...)):
    try:
        save_config(config)
        print("Config updated successfully.")
        return {"message": "Config saved successfully!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Error saving config: " + str(e))

@app.get("/generate")
def generate():
    try:
        config = load_config()
        if config is None:
             raise Exception("Config is empty")

        timetable, class_timetable = generate_timetable(config)
        data = {
            "teachers_timetable": timetable,
            "classes_timetable": class_timetable
        }

        with open(TIMETABLE_FILE, "w") as f:
            json.dump(data, f, indent=4)

        print("Timetable generated and saved.")
        return {"message": "Timetable saved successfully!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Error generating timetable: " + str(e))

@app.get("/ui.html")
def get_ui():
    return FileResponse("ui.html")

@app.get("/")
def root():
    return FileResponse("ui.html")

@app.get("/{file_path:path}")
def serve_static(file_path: str):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(base_dir, DATA_DIRECTORY)

    # Check if file exists in root (safe)
    target_path = os.path.abspath(os.path.join(base_dir, file_path))
    if target_path.startswith(base_dir) and os.path.exists(target_path) and os.path.isfile(target_path):
        return FileResponse(target_path)

    # Check data directory (safe fallback)
    target_data_path = os.path.abspath(os.path.join(data_dir, file_path))
    if target_data_path.startswith(data_dir) and os.path.exists(target_data_path):
        if os.path.isdir(target_data_path):
            index_path = os.path.join(target_data_path, "index.html")
            if os.path.exists(index_path):
                return FileResponse(index_path)
        elif os.path.isfile(target_data_path):
            return FileResponse(target_data_path)

    raise HTTPException(status_code=404, detail="File not found")

if __name__ == "__main__":
    # Change the directory before starting the server
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    run_ui()
    print(f"Server running at http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
