import webbrowser
import json
import os
from typing import Dict, Any
from contextlib import asynccontextmanager

from generation_jobs import GenerationBusy, GenerationJobs
from scheduler import ConfigError

import uvicorn
from fastapi import FastAPI, HTTPException, Body
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

jobs = GenerationJobs()


@asynccontextmanager
async def lifespan(app):
    yield
    jobs.shutdown()


app = FastAPI(lifespan=lifespan)

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

def start_generation(config):
    try:
        return jobs.submit(config, TIMETABLE_FILE)
    except ConfigError as exc:
        raise HTTPException(status_code=422, detail={"message": "Invalid configuration.", "issues": exc.issues}) from exc
    except GenerationBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/generation-jobs", status_code=202)
def create_generation_job(config: Dict[str, Any] = Body(...)):
    job_id, _ = start_generation(config)
    return {"job_id": job_id, "status_url": f"/generation-jobs/{job_id}"}


@app.get("/generation-jobs/{job_id}")
def get_generation_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Generation job not found. Job history resets when the server restarts.")
    return job


@app.get("/generate")
def generate():
    # Compatibility endpoint: uses the saved configuration and waits for the same
    # single-worker pipeline used by the new asynchronous browser flow.
    _, future = start_generation(load_config())
    result = future.result()
    if result["status"] == "succeeded":
        return result
    code = {"infeasible": 422, "timeout": 408}.get(result["status"], 500)
    raise HTTPException(status_code=code, detail=result["message"])


@app.get("/timetable")
@app.get("/timetables")
def get_timetable():
    if not os.path.exists(TIMETABLE_FILE):
        return JSONResponse(status_code=404, content={"message": "Timetable not found. Please generate it first."})
    try:
        with open(TIMETABLE_FILE, "r") as f:
            data = json.load(f)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail="Error loading timetable: " + str(e))

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
